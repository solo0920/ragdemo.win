"""主機身份 registry：自報硬體/IP/模型，心跳寫入 Postgres，供 /hosts 查詢。"""
import json
import os
import platform
import socket
import subprocess
import uuid

try:
    import asyncpg
except ImportError:
    asyncpg = None

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://rag:changeme@postgres:5432/ragdemo")
HOST_ID = os.getenv("HOST_ID", "")
HOSTNAME = os.getenv("HOST_NAME", "") or socket.gethostname()
TS_IP = os.getenv("TS_IP", "")
LAN_IP = os.getenv("LAN_IP", "")
HEARTBEAT = int(os.getenv("REGISTRY_HEARTBEAT", "30"))
HOST_MACHINE_ID = os.getenv("HOST_MACHINE_ID_FILE", "/run/secrets/host-machine-id")
HOST_HOSTNAME = os.getenv("HOST_HOSTNAME_FILE", "/run/secrets/host-hostname")

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

_pool = None


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
    v = _read_id([HOST_HOSTNAME, "/etc/hostname"])
    return v or HOSTNAME


def _system_id() -> str:
    v = _read_id([HOST_MACHINE_ID, "/etc/machine-id"])
    if v:
        return v
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


def _ips() -> list[str]:
    seen, out = set(), []
    for ip in (TS_IP, LAN_IP):
        if ip and ip not in seen:
            seen.add(ip)
            out.append(ip)
    try:
        for info in socket.getaddrinfo(HOSTNAME, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in seen:
                seen.add(ip)
                out.append(ip)
    except Exception:
        pass
    return out


async def _pool_get():
    global _pool
    if _pool is None:
        if asyncpg is None:
            raise RuntimeError("asyncpg 未安裝")
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=3)
    return _pool


async def heartbeat(models: list[str], llm: str, ok: bool = True) -> None:
    global _pool
    try:
        pool = await _pool_get()
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
    except Exception as e:
        if _pool is not None:
            try:
                await _pool.close()
            except Exception:
                pass
            _pool = None
        raise RuntimeError(f"registry heartbeat failed: {e}") from e


async def list_hosts() -> list[dict]:
    rows: list[dict] = []
    try:
        pool = await _pool_get()
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
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
        _pool = None