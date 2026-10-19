FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends g++ python3 ca-certificates && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin bot \
 && mkdir -p /tmp/bot && chmod 0777 /tmp/bot
COPY sandbox/entrypoint.py /opt/oantuti/entrypoint.py
COPY sandbox/cpp_bot.hpp sandbox/cpp_main.hpp sandbox/python_main.py /opt/oantuti/
ENTRYPOINT ["python3", "/opt/oantuti/entrypoint.py"]
