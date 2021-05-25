#!/bin/bash
set -e -x

function repair_wheel {
    wheel=$1
    if ! auditwheel show "$wheel"; then
        echo "Skipping non-platform wheel $wheel"
    else
        auditwheel repair "$wheel" --plat "$PLAT" -w /io/wheelhouse
    fi
}

cd /io/

for tag in $PYTHON_TAGS; do
    PYBIN="/opt/python/$tag/bin/"
    ${PYBIN}/pip install tox
    CFLAGS="-std=c99 -O3" ${PYBIN}/tox -e build -- -w
done

mv dist/ raw_wheels

for whl in raw_wheels/*.whl; do
    repair_wheel "$whl"
done

mkdir dist
mv wheelhouse/*.whl dist/
