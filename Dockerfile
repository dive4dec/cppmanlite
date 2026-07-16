# ---- Stage 1: Download and process cppreference archive ----
FROM python:3.12-slim AS builder

ARG CPPREFERENCE_VERSION="v20250209"
ARG PUBLIC_PATH="/cppmanlite/"

WORKDIR /build

# Download the cppreference HTML archive
RUN apt-get update && apt-get install -y --no-install-recommends curl xz-utils && \
    curl -sL "https://github.com/PeterFeicht/cppreference-doc/releases/download/${CPPREFERENCE_VERSION}/html-book-$(echo ${CPPREFERENCE_VERSION} | sed 's/v//').tar.xz" \
    -o cppref.tar.xz && \
    mkdir -p extract && \
    tar xf cppref.tar.xz -C extract && \
    rm cppref.tar.xz

# Build the search index and strip HTML pages
COPY scripts/build_index.py scripts/build_index.py
RUN python3 scripts/build_index.py extract/reference/en /build/output

# ---- Stage 2: nginx serving static files ----
FROM nginx:alpine

ARG PUBLIC_PATH="/cppmanlite/"

# Copy static site
COPY site/ /usr/share/nginx/html${PUBLIC_PATH}

# Copy processed docs + index
COPY --from=builder /build/output/docs/ /usr/share/nginx/html${PUBLIC_PATH}docs/
COPY --from=builder /build/output/index.json /usr/share/nginx/html${PUBLIC_PATH}index.json

# nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Fix permissions (nginx worker runs as non-root)
RUN chmod -R a+r /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
