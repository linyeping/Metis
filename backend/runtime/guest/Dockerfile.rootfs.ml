# Phase 8.3 ML/DL layer — built on top of metis-rootfs-rich-03.
# Reuses the 4.36GB rich image as base and adds the ML/DL stack:
# torch (CPU), scikit-learn, transformers, sentence-transformers, scipy,
# statsmodels, datasets, accelerate.
FROM metis-rootfs-rich-03

ARG HTTP_PROXY=
ARG HTTPS_PROXY=
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY}

# uv pip install with proxy. CPU-only torch wheels are the manylinux default.
RUN uv pip install --system --no-cache \
        torch \
        scikit-learn \
        scipy statsmodels \
        transformers tokenizers safetensors \
        sentence-transformers \
        datasets accelerate \
        joblib

# Clear proxy from the final image so the VM doesn't try to use 7897 at runtime.
ENV HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy=
