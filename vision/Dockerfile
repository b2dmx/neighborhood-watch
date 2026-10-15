FROM python:3.12-slim

# Intel GPU runtime (Arc / Core Ultra iGPU) so OpenVINO can use the GPU; the CPU works without it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ocl-icd-libopencl1 intel-opencl-icd \
 && rm -rf /var/lib/apt/lists/* || true

RUN pip install --no-cache-dir openvino numpy pillow aiohttp opencv-python-headless

WORKDIR /app
COPY app.py scan.py ./

ENV NW_MODEL=/models/megadetector \
    NW_DEVICE=AUTO \
    NW_SIZE=640 \
    NW_PORT=8765 \
    NW_SCAN_ROOT=/scan \
    NW_WORK=/work
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')"
CMD ["python", "app.py"]
