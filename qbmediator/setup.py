from setuptools import setup, find_packages

setup(
    name='qbmediator',
    version='0.1.0',
    packages=find_packages(include=['qbmediator', 'qbmediator.*']),
    install_requires=[
        'redis',
        'pika',
        'jsons',
        'kombu',
        'kafka-python',
        'sacremoses',
    ]
)
