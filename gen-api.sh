#!/bin/sh

sphinx-apidoc -o doc/source --no-toc -f -M contrail_healer

find doc/source -name '*.rst' -print | xargs sed -i '/Submodules/ { N; d; }'
find doc/source -name '*.rst' -print | xargs sed -i '/:undoc-members:/d'
