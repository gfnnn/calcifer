# All-in-one image: web app + Discord bot + scheduled sync.
FROM python:3.12-slim

# Run as an unprivileged user (defense-in-depth for an internet-facing process):
# a future dependency/path-traversal bug then can't act as root in the container.
# Create the user up front so /app and the /data mount point are owned by it.
RUN useradd --create-home --uid 10001 app

WORKDIR /app

# gosu lets the entrypoint drop from root to a non-root user *after* fixing
# ownership of the mounted data volume (which can mount root-owned at runtime).
# A non-root runtime user is defense-in-depth for an internet-facing process.
# (The `app` user itself is created once, up front, above.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /data \
    && chown -R app:app /app /data

# Persist the SQLite database on a mounted volume at /data. The mount itself is
# configured by the host at runtime (Railway Volumes / Render disk); the
# Dockerfile VOLUME instruction is intentionally omitted because Railway's
# builder rejects it ("docker VOLUME ... is not supported, use Railway Volumes").
# /data is created and owned by `app` in the COPY step above so the non-root user
# can write the database. The host must mount the volume writable by this user
# (Railway/Render do this for you).
ENV DATABASE_PATH=/data/ftabot.db \
    WEB_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1

# Hosts inject $PORT; the app reads it (falls back to 8080).
EXPOSE 8080

# Intentionally NO `USER app` here. The container must start as root so the
# entrypoint can chown the (possibly root-owned) mounted data volume and then
# drop to the unprivileged `app` user via `gosu`. A `USER app` directive would
# break this two ways: gosu needs root to switch users (it would crash startup
# with "operation not permitted"), and a non-root process can't chown a
# root-owned volume mount. Non-root at runtime is enforced by `gosu app` below.
#
# The entrypoint runs as root only long enough to make the data volume writable,
# then execs the process as the unprivileged `app` user (via gosu). Hosts that
# override the command (e.g. Railway's startCommand) still pass through it.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "ftabot.serve"]
