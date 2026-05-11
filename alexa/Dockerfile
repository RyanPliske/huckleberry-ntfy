# Lambda container image: repo root is docker build context (see template.yaml Metadata).
FROM public.ecr.aws/lambda/python:3.12

RUN pip install --no-cache-dir \
    "aiohttp>=3.10.0" \
    "pydantic>=2.10.0" \
    "google-cloud-firestore>=2.11.0" \
    "tzdata>=2024.1" \
    "openai>=2.36.0" \
    "pydantic-ai>=1.93.0" \
    "ask-sdk-core>=1.19.0" \
    "ask-sdk-model>=1.82.0" \
    "ask-sdk-runtime>=1.19.0"

COPY src/huckleberry_api ${LAMBDA_TASK_ROOT}/huckleberry_api
COPY alexa/app.py ${LAMBDA_TASK_ROOT}/app.py

CMD ["app.lambda_handler"]
