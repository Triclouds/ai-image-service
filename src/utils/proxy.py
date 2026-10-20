"""出站代理的统一注入点。

AI 中转站（api.relayrouter.ai）需要海外出口，走 PROXY_URL 指定的代理；
钉钉接口在国内，由调用方显式 trust_env=False 保持直连。
"""

from config import Settings


def proxy_kwargs(settings: Settings) -> dict[str, str]:
    """返回 httpx 客户端的构造参数。

    - PROXY_URL 非空：强制走该代理（覆盖系统/环境代理）
    - PROXY_URL 为空：不传 proxy，httpx 沿用 trust_env 的系统/环境代理行为
    """
    return {"proxy": settings.proxy_url} if settings.proxy_url else {}
