"""MCP server — query NUEiP attendance / leaves / approvals.

stdio transport. Credentials come from env vars:
    NUEIP_COMPANY     公司代號
    NUEIP_ACCOUNT     員工帳號
    NUEIP_PASSWORD    密碼
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import Credentials, NueipClient, NueipError

mcp = FastMCP("nueip")

_client: NueipClient | None = None


def _get_client() -> NueipClient:
    global _client
    if _client is not None:
        return _client
    missing = [k for k in ("NUEIP_COMPANY", "NUEIP_ACCOUNT", "NUEIP_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise NueipError(f"missing env vars: {', '.join(missing)}")
    verify_tls = os.environ.get("NUEIP_VERIFY_TLS", "false").lower() in ("1", "true", "yes")
    _client = NueipClient(
        Credentials(
            company=os.environ["NUEIP_COMPANY"],
            account=os.environ["NUEIP_ACCOUNT"],
            password=os.environ["NUEIP_PASSWORD"],
        ),
        verify_tls=verify_tls,
    )
    return _client


@mcp.tool()
def my_attendance(start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    """查我自己的出勤紀錄。日期格式 YYYY-MM-DD；省略則查今日。"""
    return _get_client().my_attendance(start_date=start_date, end_date=end_date)


@mcp.tool()
def my_leave_balance(year: int | None = None, raw: bool = False) -> dict[str, Any]:
    """查我自己的假期餘額（特休、病假等）。預設查當年度全年。

    瘦身模式（預設）：只回**當下這個期間**（today 落在 period_s_date~period_e_date
    內）的桶，按 v_name group_by 後算出與 NUEiP UI 一致的剩餘時數：

        剩餘 = 最早桶 prev_left + Σ resource_hours − Σ used_total_hours

    跨年度假別（如特休）不會把下年度配額疊進來。raw=True 可取回 NUEiP 原始
    payload（~50KB），包含全部桶與每假別 ~45 個欄位。
    """
    return _get_client().my_leave_balance(year=year, raw=raw)


@mcp.tool()
def team_leaves(
    start_date: str | None = None,
    end_date: str | None = None,
    scope: str = "dept",
    filt_method: str = "passed",
) -> dict[str, Any]:
    """查團隊 / 部門當日（或區間）的請假狀況。

    scope: "dept" = 整個部門 (預設) / "team" = 我的子部門。
    filt_method: "passed" = 已通過 (預設) / "ongoing" = 簽核中 / "all" = 全部。
    """
    return _get_client().team_leaves(
        start_date=start_date, end_date=end_date, scope=scope, filt_method=filt_method
    )


@mcp.tool()
def pending_approvals(
    type_: str = "leave",
    scope: str = "team",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """查待我簽核的項目。

    type_: "leave" (預設) / "overtime" / "attendance" / "business_trip" 等。
    scope: "team" (預設) = 我的子部門 / "dept" = 整個部門。

    type_="attendance" 時，每筆會多回：
      - day_punches: 當天實際打卡（已濾掉這筆補卡申請本身）
      - pending_correction: {section, time} 補卡申請的段別與時間
      - summary: 應下班時間 / 實際下班 / 還差幾秒到滿勤
        (worktime_short_seconds_after_correction 正值=還早退/遲到)
    """
    return _get_client().pending_approvals(
        type_=type_, scope=scope, start_date=start_date, end_date=end_date
    )


@mcp.tool()
def team_attendance(
    start_date: str | None = None,
    end_date: str | None = None,
    scope: str = "dept",
    name_filter: str | None = None,
    raw: bool = False,
) -> dict[str, Any]:
    """查團隊／部門出勤紀錄（manager view）。日期格式 YYYY-MM-DD。

    scope: "dept" = 整個部門 (預設) / "team" = 我的子部門。
    name_filter: 姓名子字串過濾（case-insensitive，match NUEiP usr_name，可含中英文）。
    raw=True: 回 NUEiP 原始 payload（單月 30 人約 1-2 MB）；
    預設 slim view 回 {rows, count}，每列為 (user, date) 一筆，欄位：
    date, u_sn, name, dept, off_day, holiday, worktime, on_punch, off_punch,
    late, late_min, leave_early, leave_early_min, durhour, durmin, has_leave。

    NUEiP 的 `late` 欄位是以 NUEiP 公司排班時間（如 09:00）為基準；要用其他
    遲到定義（例：> 10:00），請改看 on_punch 時間自行判定。
    """
    return _get_client().team_attendance(
        start_date=start_date,
        end_date=end_date,
        scope=scope,
        name_filter=name_filter,
        raw=raw,
    )


@mcp.tool()
def whoami() -> dict[str, Any]:
    """Diagnostic — 顯示登入後解析出的公司/部門/使用者 ID。"""
    return _get_client().whoami()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
