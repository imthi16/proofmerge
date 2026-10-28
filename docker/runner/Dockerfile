# Image that runs probes. It holds only Python and pytest; the code under test is
# mounted read-only at run time, and the container runs with no network.
# shortcut: the base tag is not pinned by digest; the image ID is recorded in every
# Execution instead, so evidence still names the exact image. Pin before office use.
FROM python:3.13-slim
RUN pip install --no-cache-dir pytest==9.1.1
USER 65534:65534
WORKDIR /work
