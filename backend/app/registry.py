"""主機身份 registry：自報硬體/IP/模型，心跳寫入 Postgres，供 /hosts 查詢。"""
import json
import logging
import os
import platform
import socket
import subprocess
import uuid

from .common import pg

logger = logging.getLogger("ragdemo")

HOST_ID = os.getenv("HOST_ID", "")
HOSTNAME = os.getenv("HOST_NAME", "") or socket.gethostname()
TS_IP = os.getenv("TS_IP", "")
LAN_IP = os.getenv("LAN_IP", "")
HEARTBEAT = int(os.getenv("REGISTRY_HEARTBEAT", "30"))
STALE_MIN = int(os.getenv("REGISTRY_STALE_MIN", "3"))
HOST_MACHINE_ID = os.getenv("HOST_MACHINE_ID_FILE", "/run/secrets/host-machine-id")
HOST_HOSTNAME = os.getenv("HOST_HOSTNAME_FILE", "/run/secrets/host-hostname")
# 值優先於檔案。為什麼需要這條路徑：Docker Desktop（WSL）掛「單一檔案」型 bind mount
# 不可靠，實測容器 init 直接 exit=127（error mounting ... not a directory），
# 而同樣的「目錄型」掛載正常 —— 只有 /etc/hostname、/etc/machine-id 這兩個單檔掛載會死。
# 改走環境變數後不再依賴 host 檔案佈局，也符合「per-machine 差異留在 .env」的紀律。
# 檔案仍保留為 fallback（x570/mbp 的單檔掛載在原生 Linux/macOS 上是好的）。
_MACHINE_ID_ENV = os.getenv("HOST_MACHINE_ID", "").strip()
_HOSTNAME_ENV = os.getenv("HOST_NAME", "").strip()

DDL = """
CREATE TABLE IF NOT EXISTS backends (
    host_id    TEXT PRIMARY KEY,
    hostname   TEXT NOT NULL DEFAULT '',
    machine_id TEXT NOT NULL DEFAULT '',
    mac        TEXT NOT NULL DEFAULT '',
    ips        TEXT[] NOT NULL DEFAULT '{}',
    ts_ip      TEXT NOT NULL DEFAULT '',
    lan_ip     TEXT NOT NULL DEFAULT '',
    models     JSONB NOT NULL DEFAULT '[]',
    llm        TEXT NOT NULL DEFAULT '',
    last_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ok         BOOLEAN NOT NULL DEFAULT TRUE
);
"""


def _read_id(paths: list[str]) -> str:
    for p in paths:
        try:
            with open(p) as f:
                v = f.read().strip()
                if v:
                    return v
        except Exception:
            continue
    return ""


def _my_hostname() -> str:
    v = _HOSTNAME_ENV or _read_id([HOST_HOSTNAME, "/etc/hostname"])
    return v or HOSTNAME


def _system_id() -> str:
    v = _MACHINE_ID_ENV or _read_id([HOST_MACHINE_ID, "/etc/machine-id"])
    if v:
        return v
    # 走到這裡代表 machine-id 既沒環境變數也沒檔案。退化成 MAC 是弱識別
    # （VM/容器下甚至可能重複），所以明確記警告，不要靜默降級。
    logger.warning("machine_id 無法取得（HOST_MACHINE_ID 未設且檔案不存在）→ 退化成 MAC 位址，"
                   "該機在 registry 的識別不可靠。請在該機 .env 補 HOST_MACHINE_ID。")
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=5,
            )
            for line in out.stdout.splitlines():
                if "IOPlatformUUID" in line:
                    return line.split('"')[-2]
    except Exception:
        pass
    return str(uuid.getnode())


def _mac() -> str:
    n = uuid.getnode()
    return ":".join(f"{(n >> s) & 0xFF:02X}" for s in range(40, -1, -8))


def _in_tailscale(ip: str) -> bool:
    """是否落在 tailscale CGNAT 100.64.0.0/10（100.64.0.0–100.127.255.255）。

    IP 準則（2026-09-22 定案）：registry 的 `ips` 一律只留 tailscale IP，
    LAN/容器/loopback 一律排除，避免錯誤位址污染與前端選址錯亂。
    """
    try:
        parts = ip.split(".")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            return False
        return int(parts[0]) == 100 and 64 <= int(parts[1]) <= 127
    except Exception:
        return False


def _ips() -> list[str]:
    seen, out = set(), []
    for ip in (TS_IP, LAN_IP):
        if ip and ip not in seen and _in_tailscale(ip):
            seen.add(ip)
            out.append(ip)
    try:
        for info in socket.getaddrinfo(HOSTNAME, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in seen and _in_tailscale(ip):
                seen.add(ip)
                out.append(ip)
    except Exception:
        pass
    return out


async def heartbeat(models: list[str], llm: str, ok: bool = True) -> None:
    try:
        pool = await pg.pool_get()
        async with pool.acquire() as con:
            await con.execute(DDL)
            await con.execute(
                """
                INSERT INTO backends
                  (host_id, hostname, machine_id, mac, ips, ts_ip, lan_ip, models, llm, ok)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT (host_id) DO UPDATE SET
                  hostname=EXCLUDED.hostname, machine_id=EXCLUDED.machine_id, mac=EXCLUDED.mac,
                  ips=EXCLUDED.ips, ts_ip=EXCLUDED.ts_ip, lan_ip=EXCLUDED.lan_ip,
                  models=EXCLUDED.models, llm=EXCLUDED.llm, last_seen=now(), ok=EXCLUDED.ok
                """,
                HOST_ID, _my_hostname(), _system_id(), _mac(), _ips(), TS_IP, LAN_IP,
                json.dumps(models, ensure_ascii=False), llm, ok,
            )
            # 清掉超過 STALE_MIN 分鐘未報到的 host（離線/改名殘留自動消失）。
            # 只在心跳連得上 PG 時執行：x570 離線時 PG 也離線，此句自然不跑、無害。
            await con.execute(
                "DELETE FROM backends WHERE last_seen < now() - make_interval(mins => $1)",
                STALE_MIN,
            )
    except Exception as e:
        # 連線斷掉時丟掉死掉的池，下次呼叫才會重建（自癒）。
        await pg.drop_pool()
        raise RuntimeError(f"registry heartbeat failed: {e}") from e


async def list_hosts() -> list[dict]:
    rows: list[dict] = []
    try:
        pool = await pg.pool_get()
        async with pool.acquire() as con:
            await con.execute(DDL)
            r = await con.fetch("SELECT * FROM backends ORDER BY last_seen DESC")
            for row in r:
                rows.append({
                    "host_id": row["host_id"],
                    "hostname": row["hostname"],
                    "machine_id": row["machine_id"],
                    "mac": row["mac"],
                    "ips": row["ips"],
                    "ts_ip": row["ts_ip"],
                    "lan_ip": row["lan_ip"],
                    "models": json.loads(row["models"]) if isinstance(row["models"], str) else row["models"],
                    "llm": row["llm"],
                    "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
                    "ok": row["ok"],
                })
    except Exception:
        pass
    return rows


async def close() -> None:
    await pg.drop_pool()