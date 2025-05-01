FROM pytorch/torchserve:latest as builder

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3-dev \
        python3-pip \
        wget \
        gnupg \
        make \
        g++ \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /root

ENV ONEAPI_VERSION=2023.0.0
RUN wget -q https://apt.repos.intel.com/intel-gpg-keys/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB && \
    apt-key add *.PUB && \
    rm *.PUB && \
    echo "deb https://apt.repos.intel.com/oneapi all main" > /etc/apt/sources.list.d/oneAPI.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        intel-oneapi-mkl-devel-$ONEAPI_VERSION \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN python3 -m pip --no-cache-dir install cmake==3.22.*

ENV ONEDNN_VERSION=3.1.1
RUN wget -q https://github.com/oneapi-src/oneDNN/archive/refs/tags/v${ONEDNN_VERSION}.tar.gz && \
    tar xf *.tar.gz && \
    rm *.tar.gz && \
    cd oneDNN-* && \
    cmake -DCMAKE_BUILD_TYPE=Release -DONEDNN_LIBRARY_TYPE=STATIC -DONEDNN_BUILD_EXAMPLES=OFF -DONEDNN_BUILD_TESTS=OFF -DONEDNN_ENABLE_WORKLOAD=INFERENCE -DONEDNN_ENABLE_PRIMITIVE="CONVOLUTION;REORDER" -DONEDNN_BUILD_GRAPH=OFF . && \
    make -j$(nproc) install && \
    cd .. && \
    rm -r oneDNN-*

COPY third_party third_party
COPY cli cli
COPY include include
COPY src src
COPY cmake cmake
COPY python python
COPY CMakeLists.txt .

ARG CXX_FLAGS
ENV CXX_FLAGS=${CXX_FLAGS:-"-msse4.1"}
ENV CTRANSLATE2_ROOT=/opt/ctranslate2

RUN mkdir build && \
    cd build && \
    cmake -DCMAKE_INSTALL_PREFIX=${CTRANSLATE2_ROOT} \
          -DWITH_CUDA=OFF -DWITH_CUDNN=OFF -DWITH_MKL=ON -DWITH_DNNL=ON -DOPENMP_RUNTIME=COMP \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS="${CXX_FLAGS}" .. && \
    VERBOSE=1 make -j$(nproc) install

ENV LANG=en_US.UTF-8
COPY README.md .

RUN cd python && \
    python3 -m pip --no-cache-dir install -r install_requirements.txt && \
    python3 setup.py bdist_wheel --dist-dir $CTRANSLATE2_ROOT

FROM pytorch/torchserve:latest

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgomp1 \
        python3-pip \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV CTRANSLATE2_ROOT=/opt/ctranslate2
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$CTRANSLATE2_ROOT/lib

COPY --from=builder $CTRANSLATE2_ROOT $CTRANSLATE2_ROOT
RUN python3 -m pip --no-cache-dir install $CTRANSLATE2_ROOT/*.whl && \
    rm $CTRANSLATE2_ROOT/*.whl

USER model-server
