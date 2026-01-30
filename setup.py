from setuptools import setup, find_packages

setup(
    name="cr-infer",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "typer[all]",
        "google-cloud-run",
        "google-cloud-storage",
        "google-cloud-build",
        "google-cloud-logging",
        "google-cloud-quotas",
        "google-cloud-resource-manager",
        "google-auth",
        "InquirerPy",
        "pydantic",
        "rich",
        "google-api-python-client"
    ],
    entry_points={
        "console_scripts": [
            "cr-infer=cr_infer.cli.main:app",
        ],
    },
)
