"""HTTP client for NUEiP cloud HR (cloud.nueip.com).

Personal-use, manager-view: query own attendance / leave balance, team leaves,
pending approvals. Endpoints reverse-engineered from the web UI (Network
captures). NUEiP doesn't issue free-tier API keys, so we simulate the browser
login and reuse its session cookie.
"""

from __future__ import annotations

import base64
import datetime as dt
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup

BASE = "https://cloud.nueip.com"
LOGIN_URL = f"{BASE}/login/index/param"
HOME_URL = f"{BASE}/home"
LEAVE_USER_URL = f"{BASE}/leave_application/personal_leave_application_user/"

ATTENDANCE_URL = f"{BASE}/attendance_record/ajax"
LEAVE_BALANCE_URL = f"{BASE}/personal_leave_resource"
LEAVE_MANAGER_URL = f"{BASE}/leave_application/personal_leave_application_manager/"
AUDIT_URL_FMT = f"{BASE}/leader_audit_work_list/index/{{type_}}"

# fe-pno values copied from real NUEiP page IDs; required header on most XHRs.
FE_PNO = {
    "attendance": "124",
    "leave_balance": "44",
    "team_leaves": "303",
    "audit": "225",
}

SESSION_TTL = 45 * 60  # re-login this often even if cookies still look valid

CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


class NueipError(Exception):
    pass


@dataclass
class Credentials:
    company: str
    account: str
    password: str


@dataclass
class UserContext:
    """Hierarchy IDs needed to scope every API call."""

    cmpny: str = ""           # e.g. "13947"  → FLayer
    dept: str = ""            # e.g. "103397" → SLayer suffix
    deptsn: str = ""          # e.g. "665654" → TLayer suffix
    user_id: str = ""         # e.g. "462892" (from cuid cookie)

    @property
    def flayer(self) -> str:
        return self.cmpny

    @property
    def slayer(self) -> str:
        return f"{self.cmpny}_{self.dept}"

    @property
    def tlayer(self) -> str:
        return f"{self.cmpny}_{self.dept}_{self.deptsn}"

    @property
    def employee(self) -> str:
        return f"{self.cmpny}_{self.dept}_{self.deptsn}"


_SCHEDULE_RE = re.compile(r"\s*(\d+):(\d+)\s*[～~\-]+\s*(\d+):(\d+)")
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_schedule_span_seconds(worktime: str) -> int | None:
    """Parse "09:00～18:00" into the required clock-in→clock-out span in seconds.

    Lunch break is already baked in — a 09:00~18:00 schedule means 9 hours
    between in and out (8h work + 1h lunch), which matches NUEiP's worktime
    expectations for that band.
    """
    m = _SCHEDULE_RE.match(worktime or "")
    if not m:
        return None
    sh, sm, eh, em = (int(x) for x in m.groups())
    return (eh * 3600 + em * 60) - (sh * 3600 + sm * 60)


def _extract_remark_text(html: str) -> str:
    """NUEiP wraps remarks like <a data-content="忘刷卡">說明</a> — the real
    reason lives in data-content, not the visible text."""
    if not html:
        return ""
    m = re.search(r'data-content=["\']([^"\']*)["\']', html)
    if m:
        return m.group(1).strip()
    return _TAG_RE.sub("", html).strip()


def _fmt_short_seconds(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    sign = "-" if seconds < 0 else ""
    s = abs(int(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{sign}{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{sign}{m}m{s:02d}s"
    return f"{sign}{s}s"


def _build_attendance_summary(
    item: dict, on_actual: list[dict], off_actual: list[dict], request_time: str
) -> dict[str, Any]:
    """Compose the worktime cross-check fields for an attendance approval item.

    `worktime_short_seconds_*` is positive when the day falls short of required
    worktime (i.e. 早退 for 下班 corrections, 遲到 for 上班 corrections), and
    negative when it exceeds. `_str` variants are human-friendly.
    """
    section = item.get("section_typ") or ""
    actual_in = on_actual[0]["time"] if on_actual else None
    actual_out = off_actual[-1]["time"] if off_actual else None

    summary: dict[str, Any] = {
        "name": item.get("usr_name_str"),
        "date": item.get("belong_date"),
        "schedule": item.get("worktime"),
        "section": section,
        "reason": _extract_remark_text(item.get("remark") or ""),
        "actual_in": actual_in,
        "actual_out": actual_out,
        "request_time": request_time,
    }

    span = _parse_schedule_span_seconds(item.get("worktime") or "")
    if span is None:
        return summary
    summary["required_seconds"] = span

    def _parse(t: str) -> dt.datetime | None:
        try:
            return dt.datetime.strptime(t, "%H:%M:%S")
        except (TypeError, ValueError):
            return None

    if section == "下班" and actual_in:
        ti = _parse(actual_in)
        if ti is None:
            return summary
        expected_off = ti + dt.timedelta(seconds=span)
        summary["expected_off"] = expected_off.strftime("%H:%M:%S")
        to = _parse(actual_out) if actual_out else None
        tr = _parse(request_time) if request_time else None
        if to is not None:
            short_a = int((expected_off - to).total_seconds())
            summary["worktime_short_seconds_actual"] = short_a
            summary["worktime_short_str_actual"] = _fmt_short_seconds(short_a)
        if tr is not None:
            short_r = int((expected_off - tr).total_seconds())
            summary["worktime_short_seconds_after_correction"] = short_r
            summary["worktime_short_str_after_correction"] = _fmt_short_seconds(short_r)
    elif section == "上班" and actual_out:
        to = _parse(actual_out)
        if to is None:
            return summary
        required_in = to - dt.timedelta(seconds=span)
        summary["required_in"] = required_in.strftime("%H:%M:%S")
        ti = _parse(actual_in) if actual_in else None
        tr = _parse(request_time) if request_time else None
        if ti is not None:
            short_a = int((ti - required_in).total_seconds())
            summary["worktime_short_seconds_actual"] = short_a
            summary["worktime_short_str_actual"] = _fmt_short_seconds(short_a)
        if tr is not None:
            short_r = int((tr - required_in).total_seconds())
            summary["worktime_short_seconds_after_correction"] = short_r
            summary["worktime_short_str_after_correction"] = _fmt_short_seconds(short_r)
    return summary


class NueipClient:
    def __init__(self, creds: Credentials, timeout: float = 15.0, verify_tls: bool = False) -> None:
        # NUEiP's cert chain is missing the Subject Key Identifier extension,
        # so Python 3.10+ / OpenSSL 3 rejects it. MTsung's reference impl
        # also disables verification. Personal-use only; we always hit the
        # fixed host cloud.nueip.com so the MITM window is narrow. Override
        # with NUEIP_VERIFY_TLS=true if your environment can validate.
        self.creds = creds
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            verify=verify_tls,
            headers={"User-Agent": CHROME_UA, "Origin": BASE},
        )
        self._logged_in_at: float | None = None
        self._ctx: UserContext | None = None

    # ---------- session ----------

    def _session_fresh(self) -> bool:
        return self._logged_in_at is not None and (time.time() - self._logged_in_at) < SESSION_TTL

    def login(self, force: bool = False) -> None:
        if not force and self._session_fresh():
            return
        resp = self._client.post(
            LOGIN_URL,
            data={
                "inputCompany": self.creds.company,
                "inputID": self.creds.account,
                "inputPassword": self.creds.password,
            },
        )
        location = resp.headers.get("location", "")
        if resp.status_code not in (301, 302, 303) or not location.endswith("/home"):
            raise NueipError(
                f"login failed (status={resp.status_code}, redirect={location!r}); "
                "check NUEIP_COMPANY / NUEIP_ACCOUNT / NUEIP_PASSWORD"
            )
        # Touch /home so the rest of the session cookies (csrf_token, cuid, …) land.
        self._client.get(HOME_URL)
        self._logged_in_at = time.time()
        self._ctx = None  # force re-fetch of user context

    def _ensure(self) -> None:
        if not self._session_fresh():
            self.login(force=True)

    def _csrf(self) -> str:
        tok = self._client.cookies.get("csrf_token")
        if not tok:
            raise NueipError("csrf_token cookie missing — session likely expired")
        return tok

    def _ctx_or_fetch(self) -> UserContext:
        if self._ctx and self._ctx.cmpny:
            return self._ctx

        self._ensure()
        resp = self._client.get(LEAVE_USER_URL)
        if resp.status_code != 200:
            raise NueipError(f"failed to load leave page (status={resp.status_code})")
        soup = BeautifulSoup(resp.text, "html.parser")

        def grab(input_id: str) -> str:
            node = soup.find("input", attrs={"id": input_id})
            return (node.get("value") if node else "") or ""

        cmpny = grab("my_cmpny")
        dept = grab("my_dept")
        deptsn = grab("my_deptsn")
        if not (cmpny and dept and deptsn):
            raise NueipError(f"could not extract user context (cmpny={cmpny}, dept={dept}, deptsn={deptsn})")

        # cuid cookie is base64("<cmpny>.<user_id>")
        user_id = ""
        cuid = self._client.cookies.get("cuid")
        if cuid:
            try:
                decoded = base64.b64decode(cuid + "==").decode("ascii", errors="ignore")
                if "." in decoded:
                    user_id = decoded.split(".", 1)[1]
            except Exception:
                pass

        self._ctx = UserContext(cmpny=cmpny, dept=dept, deptsn=deptsn, user_id=user_id)
        return self._ctx

    # ---------- request helper ----------

    def _xhr(
        self,
        url: str,
        *,
        fe_pno: str,
        data: dict,
        extra_cookies: dict | None = None,
        referer: str | None = None,
    ) -> dict:
        self._ensure()
        if extra_cookies:
            for k, v in extra_cookies.items():
                self._client.cookies.set(k, v, domain="cloud.nueip.com")
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "X-Csrf-Token": self._csrf(),
            "Fe-Pno": fe_pno,
            "Referer": referer or url,
        }
        resp = self._client.post(url, data=data, headers=headers)
        if resp.status_code == 401 or resp.status_code == 419:
            # Re-login once on auth failures.
            self.login(force=True)
            headers["X-Csrf-Token"] = self._csrf()
            resp = self._client.post(url, data=data, headers=headers)
        if resp.status_code != 200:
            raise NueipError(f"{url} status={resp.status_code} body={resp.text[:200]}")
        try:
            return resp.json()
        except Exception as e:
            raise NueipError(f"{url} returned non-JSON: {resp.text[:200]}") from e

    # ---------- public tools ----------

    def my_attendance(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        group_size: int = 1000,
    ) -> dict:
        """List my own attendance records between two dates (default = today).

        NUEiP stores the date / layer filter in cookies (Search_124_*), so we
        seed those before POSTing the action.
        """
        ctx = self._ctx_or_fetch()
        today = dt.date.today().isoformat()
        s = start_date or today
        e = end_date or today
        cookies = {
            "Search_124_work_status": "1",
            "Search_124_FLayer": ctx.flayer,
            "Search_124_SLayer": ctx.slayer,
            "Search_124_TLayer": ctx.tlayer,
            "Search_124_date_start": s,
            "Search_124_date_end": e,
            "Search_124_showByBelongDate": "1",
            "Search_124_filterModify": "0",
        }
        data = {
            "action": "attendance",
            "loadInBatch": "1",
            "loadBatchGroupNum": str(group_size),
            "loadBatchNumber": "1",
            "work_status": "1",
        }
        return self._xhr(
            ATTENDANCE_URL,
            fe_pno=FE_PNO["attendance"],
            data=data,
            extra_cookies=cookies,
            referer=f"{BASE}/attendance_record",
        )

    def my_leave_balance(
        self,
        year: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        combined: bool = True,
        raw: bool = False,
    ) -> dict:
        """Show my leave-type balances for a year (default = current year, full-year window).

        The raw NUEiP payload is ~50KB with 52 leave types × 45 fields (hours/time/hrmin
        duplicates). By default we slim it to one entry per non-empty leave type with
        only the human-meaningful fields. Pass raw=True to get the original blob.
        """
        ctx = self._ctx_or_fetch()
        today = dt.date.today()
        yr = year or today.year
        s = start_date or f"{yr}-01-01"
        e = end_date or f"{yr}-12-31"
        data = {
            "action": "list",
            "FLayer": ctx.flayer,
            "SLayer": ctx.slayer,
            "TLayer": ctx.tlayer,
            "VLayer": "all",
            "ov2leaveCombine": "on" if combined else "off",
            "annual": str(yr),
            "filter_mode": "0",
            "start_date": s,
            "end_date": e,
            "displayDetail": "off",
            "work_status": "1",
        }
        result = self._xhr(
            LEAVE_BALANCE_URL,
            fe_pno=FE_PNO["leave_balance"],
            data=data,
            referer=LEAVE_BALANCE_URL,
        )
        if raw:
            return result
        return self._slim_leave_balance(result, yr)

    @staticmethod
    def _slim_leave_balance(result: dict, year: int) -> dict:
        """Slim NUEiP v_resource into one row per leave type, *current period only*.

        NUEiP's v_resource is a dict of buckets keyed by stringified ints. A single
        leave type can span multiple buckets (e.g. 特休 has separate buckets for the
        current and next fiscal year; 加班換休 has one bucket per overtime credit).
        UI 「剩餘時數」for a bucket-group equals:

            earliest_bucket.prev_left + Σ resource_hours − Σ used_total_hours

        Per-bucket `remain_hours` is an internal FIFO running total and must NOT be
        treated as that bucket's standalone balance. We also filter to buckets where
        today falls inside `period_s_date`..`period_e_date` so cross-year quotas
        (next year's 特休) don't inflate the current view.
        """
        def num(x: object) -> float:
            if isinstance(x, (int, float)):
                return float(x)
            if isinstance(x, str):
                s = x.strip()
                if not s or s == "-":
                    return 0.0
                try:
                    return float(s)
                except ValueError:
                    return 0.0
            return 0.0

        def fmt_hrmin(hours: float) -> str:
            sign = "-" if hours < 0 else ""
            total_min = int(round(abs(hours) * 60))
            h, m = divmod(total_min, 60)
            return f"{sign}{h}h{m:02d}m"

        items = result.get("data") or []
        if not items:
            return {"year": year, "user": {}, "leaves": []}
        entry = items[0]
        u_sn = str(entry.get("u_sn") or "")
        detail = (entry.get("userDetail") or {}).get(u_sn, {}) or {}
        user = {
            "name": detail.get("user_name"),
            "u_no": detail.get("u_no"),
            "dept": detail.get("dept_name"),
            "title": detail.get("title_name"),
        }

        today = dt.date.today().isoformat()
        # bucket = one v_resource entry; many buckets can share v_name
        buckets: list[dict] = []
        for v in (entry.get("v_resource") or {}).values():
            if not isinstance(v, dict):
                continue
            s_date = v.get("period_s_date") or ""
            e_date = v.get("period_e_date") or ""
            # current-period filter: today must fall within the bucket's window
            if s_date and e_date and not (s_date <= today <= e_date):
                continue
            buckets.append({
                "v_sn": v.get("v_sn"),
                "name": v.get("v_name"),
                "period": v.get("time_period"),
                "s_date": s_date,
                "e_date": e_date,
                "prev_left": num(v.get("prev_left")),
                "resource": num(v.get("resource_hours")),
                "used": num(v.get("used_total_hours")),
                "pending": num(v.get("pending_total_hours")),
            })

        groups: dict[str, list[dict]] = {}
        for b in buckets:
            groups.setdefault(b["name"] or "", []).append(b)

        leaves = []
        for name, bs in groups.items():
            bs_sorted = sorted(bs, key=lambda b: b["s_date"])
            earliest = bs_sorted[0]
            latest = bs_sorted[-1]
            total = sum(b["resource"] for b in bs)
            used = sum(b["used"] for b in bs)
            pending = sum(b["pending"] for b in bs)
            remain = earliest["prev_left"] + total - used
            if not (total or used or pending or remain):
                continue
            period = (
                earliest["period"]
                if earliest["period"] == latest["period"]
                else f"{earliest['s_date']}~{latest['e_date']}"
            )
            leaves.append({
                "v_sn": earliest["v_sn"],
                "name": name,
                "period": period,
                "n_buckets": len(bs),
                "total": fmt_hrmin(total),
                "used": fmt_hrmin(used),
                "remain": fmt_hrmin(remain),
                "pending": fmt_hrmin(pending),
                "total_hours": total,
                "used_hours": used,
                "remain_hours": remain,
                "pending_hours": pending,
            })
        leaves.sort(key=lambda r: (-r["remain_hours"], r["v_sn"] or 0))
        return {"year": year, "user": user, "leaves": leaves, "scope": "current_period"}

    def team_leaves(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        scope: str = "dept",
        filt_method: str = "passed",
    ) -> dict:
        """List who's on leave inside the team / dept for a date range.

        scope: "dept" = whole dept (FLayer_dept_all), "team" = own subdept only.
        filt_method: "passed" (approved), "ongoing" (in approval), "all".
        """
        ctx = self._ctx_or_fetch()
        today = dt.date.today().isoformat()
        s = start_date or today
        e = end_date or today
        if scope == "dept":
            employee = f"{ctx.cmpny}_{ctx.dept}_all"
        else:
            employee = ctx.tlayer
        data = {
            "action": "list",
            "s_date": s,
            "e_date": e,
            "employee": employee,
            "resource": "all",
            "work_status": "1",
            "qry_no": "",
            "filtMethod": filt_method,
        }
        return self._xhr(
            LEAVE_MANAGER_URL,
            fe_pno=FE_PNO["team_leaves"],
            data=data,
            referer=LEAVE_MANAGER_URL.rstrip("/"),
        )

    def pending_approvals(
        self,
        type_: str = "leave",
        start_date: str | None = None,
        end_date: str | None = None,
        scope: str = "team",
    ) -> dict:
        """Items waiting for my approval.

        type_: "leave", "overtime", "attendance", "business_trip", ... (depends on tenant).
        scope: "team" = my subdept, "dept" = whole dept.

        For type_=="attendance", each item is enriched with:
          - `day_punches`: actual on/off punches of that day, with the pending
            correction itself filtered out (mirrors the EIP UI tooltip).
          - `pending_correction`: {section, time} of the request.
          - `summary`: actual_in / actual_out / expected_off (or required_in) /
            request_time + `worktime_short_seconds_actual` and
            `worktime_short_seconds_after_correction` (positive = under the
            required worktime, e.g. 早退 / 遲到).
        """
        ctx = self._ctx_or_fetch()
        if scope == "dept":
            employee = f"{ctx.cmpny}_{ctx.dept}_all"
        else:
            employee = ctx.tlayer
        url = AUDIT_URL_FMT.format(type_=type_)
        data = {
            "action": "list",
            "s_date": start_date or "",
            "e_date": end_date or "",
            "employee": employee,
            "work_status": "1",
            "filtMethod": "ongoing",
            "audit_show_dept_title": "on",
        }
        result = self._xhr(url, fe_pno=FE_PNO["audit"], data=data, referer=url)
        if type_ == "attendance":
            self._enrich_attendance_with_day_punches(result)
        return result

    def _enrich_attendance_with_day_punches(self, result: dict) -> None:
        """Enrich attendance approval items with actual punches and a worktime summary.

        For each item we:
        1. Fetch the belong_date punches.
        2. Strip out the punch matching this item's `work_time` — that entry is
           the *pending correction itself*, not an actual punch. The NUEiP UI
           tooltip only shows actual punches; we mirror that.
        3. Build a `summary` dict with the inputs needed to judge the request:
           actual_in / actual_out / expected_off (or required_in) / request_time
           plus how many seconds short of required worktime the day is, both
           before and after the correction would be applied. Positive seconds
           = under the required span (早退/遲到).
        """
        items = result.get("data") or []
        cache: dict[tuple[str, str], dict] = {}
        for item in items:
            u_sn = str(item.get("u_sn") or "")
            belong_date = item.get("belong_date") or ""
            d_sn = (item.get("userDetail") or {}).get(u_sn, {}).get("d_sn") or ""
            if not (u_sn and belong_date and d_sn):
                continue
            key = (d_sn, belong_date)
            if key not in cache:
                try:
                    cache[key] = self._attendance_dept_day(d_sn, belong_date)
                except NueipError:
                    cache[key] = {}
            user_block = cache[key].get(u_sn) or {}
            punch = user_block.get("punch") or {}

            request_work_time = item.get("work_time") or ""  # "YYYY-MM-DD HH:MM:SS"
            request_time = request_work_time.split(" ", 1)[1] if " " in request_work_time else ""

            def _flatten_excluding_request(rows: list) -> list[dict]:
                out = []
                for p in (rows or []):
                    if p.get("work_time") == request_work_time:
                        continue  # this is the pending correction itself
                    out.append({"time": p.get("time"), "work_time": p.get("work_time")})
                return out

            on_actual = _flatten_excluding_request(punch.get("onPunch"))
            off_actual = _flatten_excluding_request(punch.get("offPunch"))

            item["day_punches"] = {"onPunch": on_actual, "offPunch": off_actual}
            item["pending_correction"] = {
                "section": item.get("section_typ"),
                "time": request_time,
            }
            item["summary"] = _build_attendance_summary(item, on_actual, off_actual, request_time)

    def team_attendance(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        scope: str = "dept",
        name_filter: str | None = None,
        raw: bool = False,
        group_size: int = 1000,
    ) -> dict:
        """List dept/team attendance records between two dates (manager view).

        scope: "dept" = whole dept (FLayer_dept_all), "team" = own subdept.
        name_filter: case-insensitive substring match on usr_name (e.g. "john lin").
        raw=True returns the NUEiP payload unchanged (~MB for a month of a 30-person
        dept); the default slim view is one row per (user, date) with punch times,
        late_min, leave_early_min, durhour/durmin, and a `has_leave` flag.
        """
        ctx = self._ctx_or_fetch()
        today = dt.date.today().isoformat()
        s = start_date or today
        e = end_date or today

        if scope == "dept":
            slayer = ctx.slayer
            tlayer = f"{ctx.cmpny}_{ctx.dept}_all"
        else:
            slayer = ctx.slayer
            tlayer = ctx.tlayer

        cookies = {
            "Search_124_work_status": "1",
            "Search_124_FLayer": ctx.flayer,
            "Search_124_SLayer": slayer,
            "Search_124_TLayer": tlayer,
            "Search_124_date_start": s,
            "Search_124_date_end": e,
            "Search_124_showByBelongDate": "1",
            "Search_124_filterModify": "0",
        }
        data = {
            "action": "attendance",
            "loadInBatch": "1",
            "loadBatchGroupNum": str(group_size),
            "loadBatchNumber": "1",
            "work_status": "1",
        }
        result = self._xhr(
            ATTENDANCE_URL,
            fe_pno=FE_PNO["attendance"],
            data=data,
            extra_cookies=cookies,
            referer=f"{BASE}/attendance_record",
        )
        if raw and not name_filter:
            return result
        return self._slim_team_attendance(result, name_filter)

    @staticmethod
    def _slim_team_attendance(result: dict, name_filter: str | None = None) -> dict:
        """Slim attendance payload to per-(user, date) rows.

        Keeps off-days / holidays as rows with empty punch fields so callers
        can still see the calendar shape. `on_punch` is the first valid
        on-punch HH:MM:SS; `off_punch` is the last off-punch.
        """
        nf = (name_filter or "").lower()
        rows: list[dict] = []
        data = result.get("data") or {}
        for date_str in sorted(data.keys()):
            per_user = data[date_str] or {}
            for u_sn, rec in per_user.items():
                user = rec.get("user") or {}
                name = user.get("name") or ""
                if nf and nf not in name.lower():
                    continue
                dinfo = rec.get("dateInfo") or {}
                att = rec.get("attendance") or {}
                punch = rec.get("punch")
                if isinstance(punch, dict):
                    on_p = punch.get("onPunch") or []
                    off_p = punch.get("offPunch") or []
                else:
                    on_p, off_p = [], []
                on_time = on_p[0].get("time") if on_p else None
                off_time = off_p[-1].get("time") if off_p else None
                rows.append({
                    "date": date_str,
                    "u_sn": u_sn,
                    "name": name,
                    "dept": user.get("deptname"),
                    "off_day": bool(dinfo.get("date_off")),
                    "holiday": dinfo.get("holiday") or "",
                    "worktime": rec.get("worktime") or "",
                    "on_punch": on_time,
                    "off_punch": off_time,
                    "late": att.get("late") == "1",
                    "late_min": int(att.get("latemin") or 0),
                    "leave_early": att.get("leave_early") == "1",
                    "leave_early_min": int(att.get("leaveearlymin") or 0),
                    "durhour": att.get("durhour"),
                    "durmin": att.get("durmin"),
                    "has_leave": bool(rec.get("timeoff")),
                })
        return {"rows": rows, "count": len(rows)}

    def _attendance_dept_day(self, d_sn: str, belong_date: str) -> dict:
        """Fetch attendance for a single dept-day, keyed by u_sn.

        Uses dept-wide scope (cmpny_<d_sn>_all). The approver of an attendance
        correction always has at least view permission for the requester's
        department, so this should not 403 in normal use.
        """
        ctx = self._ctx_or_fetch()
        cookies = {
            "Search_124_work_status": "1",
            "Search_124_FLayer": ctx.flayer,
            "Search_124_SLayer": f"{ctx.cmpny}_{d_sn}",
            "Search_124_TLayer": f"{ctx.cmpny}_{d_sn}_all",
            "Search_124_date_start": belong_date,
            "Search_124_date_end": belong_date,
            "Search_124_showByBelongDate": "1",
            "Search_124_filterModify": "0",
        }
        data = {
            "action": "attendance",
            "loadInBatch": "1",
            "loadBatchGroupNum": "1000",
            "loadBatchNumber": "1",
            "work_status": "1",
        }
        resp = self._xhr(
            ATTENDANCE_URL,
            fe_pno=FE_PNO["attendance"],
            data=data,
            extra_cookies=cookies,
            referer=f"{BASE}/attendance_record",
        )
        return (resp.get("data") or {}).get(belong_date) or {}

    def whoami(self) -> dict:
        """Diagnostic — return the resolved user context."""
        ctx = self._ctx_or_fetch()
        return {
            "company_id": ctx.cmpny,
            "dept_id": ctx.dept,
            "subdept_id": ctx.deptsn,
            "user_id": ctx.user_id,
            "flayer": ctx.flayer,
            "slayer": ctx.slayer,
            "tlayer": ctx.tlayer,
        }

    def close(self) -> None:
        self._client.close()
