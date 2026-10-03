FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
# ⚠️ 920 不是 8000：8000 是 OpenAI/vLLM/ollama 的慣例埠，本機很容易被別的
# 推論服務先綁走（2026-10-03 實測：Homebrew 的 omlx-server 搶先綁 8000、我們的
# api 容器 publish 被遮蔽卻不報錯，對外 tunnel 因此服務到錯的程式）。
# 920 是三機統一的 api 埠。
#
# 這裡的 920 是**建 image 時烤進去**的，必須與 compose.yaml ports 的**右側**
# （容器埠）一致；compose 的**左側**（主機埠）才是 ${API_PORT} 可變的那一側。
# 改這兩處要一起改，只改一邊會 publish 到沒人 listening 的埠。
EXPOSE 920
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "920"]