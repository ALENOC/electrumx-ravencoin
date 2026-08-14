from pathlib import Path
import re

import setuptools


ROOT = Path(__file__).parent
version_source = (ROOT / 'electrumx' / '__init__.py').read_text(encoding='utf-8')
version = re.search(r"^version = 'ElectrumX-RVN ([^']+)'$", version_source,
                    re.MULTILINE).group(1)

with open('requirements.txt', 'r') as f:
    requirements = f.read().splitlines()

setuptools.setup(
    name='electrumX-ravencoin',
    version=version,
    scripts=['electrumx_server', 'electrumx_rpc', 'electrumx_compact_history'],
    python_requires='>=3.10,<3.13',
    install_requires=requirements,
    extras_require={
        'rocksdb': ['python-rocksdb>=0.6.9'],
        'uvloop': ['uvloop>=0.17'],
    },
    packages=setuptools.find_packages(include=('electrumx*',)),
    description='Community-maintained ElectrumX server for Ravencoin',
    author='ElectrumX-RVN community maintainers; original work by Neil Booth',
    license='MIT Licence',
    url='https://github.com/ALENOC/electrumx-ravencoin',
    long_description='Community-maintained Ravencoin server for the Electrum protocol',
    download_url=('https://github.com/ALENOC/electrumx-ravencoin/archive/'
                  f'{version}.tar.gz'),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Framework :: AsyncIO',
        'Operating System :: Unix',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        "Topic :: Database",
        'Topic :: Internet',
    ],
)
