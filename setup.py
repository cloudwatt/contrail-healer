from setuptools import setup, find_packages

install_requires = [
    'contrail-api-cli',
    'kombu',
    'kazoo',
]

test_requires = []

setup(
    name='contrail-healer',
    version='0.1',
    description="contrail-api-cli command to run live checks and fixes",
    long_description=open('README.md').read(),
    author="Jean-Philippe Braun",
    author_email="jean-philippe.braun@cloudwatt.com",
    maintainer="Jean-Philippe Braun",
    maintainer_email="jean-philippe.braun@cloudwatt.com",
    url="http://www.github.com/cloudwatt/contrail-healer",
    packages=find_packages(),
    install_requires=install_requires,
    scripts=[],
    license="MIT",
    entry_points={
        'contrail_api_cli.command': [
            'heal = contrail_healer.heal:Heal',
        ],
        'contrail_api_cli.healer': [
            'fip-healer = contrail_healer.healers.fip:FIPHealer',
        ]
    },
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: User Interfaces',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 2.7'
    ],
    keywords='contrail api cli',
    test_suite='contrail_healer.tests'
)
