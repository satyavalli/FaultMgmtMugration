FROM python:3.12-slim
WORKDIR /app
COPY backend ./backend
COPY frontend ./frontend
ENV PORT=8080 SYSLOG_PORT=5140
EXPOSE 8080 5140/udp
CMD ["python", "backend/server.py"]
