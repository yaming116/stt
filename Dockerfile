FROM python:3.11.9-slim-bullseye

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        wget \
        p7zip-full \
    && rm -rf /var/lib/apt/lists/*

# 运行端口
ENV SERVER_PORT=9977
EXPOSE 9977
#download models: tiny ， base
# tiny https://github.com/jianchang512/stt/releases/download/0.0/faster-tiny.7z
# base https://github.com/jianchang512/stt/releases/download/0.0/faster-base.7z

RUN wget https://github.com/jianchang512/stt/releases/download/0.0/faster-tiny.7z \
    && wget https://github.com/jianchang512/stt/releases/download/0.0/faster-base.7z \
    && 7z x faster-tiny.7z  -r -o/stt/internel-models \
    && 7z x faster-base.7z  -r -o/stt/internel-models \
    && rm -rf faster-*.7z

WORKDIR /stt

#COPY requirements.txt requirements.txt
RUN pip flask requests gevent faster-whisper fsspec

RUN pip install torch==2.1.2 --index-url https://download.pytorch.org/whl/cpu

COPY . .

CMD ["python" , "/stt/start.py"]