FROM mcr.microsoft.com/devcontainers/anaconda:0-3

COPY requirements-dev.txt /tmp/conda-tmp/
RUN if [ -f "/tmp/conda-tmp/requirements-dev.txt" ]; then umask 0002 && /opt/conda/bin/conda run -n base pip install -r /tmp/conda-tmp/requirements-dev.txt; fi \
    && rm -rf /tmp/conda-tmp


