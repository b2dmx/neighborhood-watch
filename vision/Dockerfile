FROM ubuntu:24.04

# Intel GPU runtime new enough for Core Ultra (Arc / Xe) iGPUs, from Intel's graphics PPA,
# so OpenVINO can run on the GPU. Without a GPU it simply runs on the CPU.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends software-properties-common gpg-agent ca-certificates \
 && add-apt-repository -y ppa:kobuk-team/intel-graphics \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv libze1 libze-intel-gpu1 intel-opencl-icd libglib2.0-0 \
 && apt-get purge -y software-properties-common gpg-agent && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /venv \
 && /venv/bin/pip install --no-cache-dir openvino numpy pillow aiohttp opencv-python-headless
ENV PATH=/venv/bin:$PATH

WORKDIR /app
COPY app.py scan.py ./

ENV NW_MODEL=/models/megadetector \
    NW_DEVICE=AUTO \
    NW_SIZE=640 \
    NW_PORT=8765 \
    NW_SCAN_ROOT=/scan \
    NW_WORK=/work \
    NW_IDLE_UNLOAD=600 \
    MALLOC_ARENA_MAX=2 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')"
CMD ["python", "app.py"]
