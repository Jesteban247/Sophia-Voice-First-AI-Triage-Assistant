# Lambda API handler for the frontend.
import base64
import json
import os
import secrets
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
import logging
import urllib.request
import urllib.parse
from urllib.error import HTTPError, URLError

# Configure structured logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables (set via Terraform)
AGENTCORE_RUNTIME_ARN = os.environ.get('AGENTCORE_RUNTIME_ARN')
AWS_REGION = os.environ.get('AGENTCORE_REGION') or os.environ.get('AWS_REGION', 'us-east-1')
S3_BUCKET = os.environ.get('S3_BUCKET', 'test-nova-images')
QUALIFIER = os.environ.get('QUALIFIER', 'DEFAULT')
VITALLENS_API_KEY = os.environ.get('VITALLENS_API_KEY')
VITALLENS_API_BASE = os.environ.get('VITALLENS_API_BASE', 'https://api.rouast.com/vitallens-v3')

# Boto3 configuration for production
# - Connection pooling for concurrent requests
# - Automatic retries with exponential backoff
# - Shorter timeouts for faster failure detection
boto_config = Config(
    region_name=AWS_REGION,
    retries={
        'max_attempts': 3,
        'mode': 'adaptive'  # Adaptive retry mode for better handling
    },
    max_pool_connections=50,  # Support concurrent Lambda invocations
    connect_timeout=5,
    read_timeout=10
)

# Initialize AWS clients with production config (reused across invocations)
s3_client = boto3.client('s3', config=boto_config)


def lambda_handler(event, context):
    # Handle API Gateway requests.
    # Extract request details
    http_method = event.get('httpMethod') or event.get('requestContext', {}).get('http', {}).get('method', 'GET')
    path = event.get('path') or event.get('rawPath', '/')
    request_id = context.aws_request_id if context else 'unknown'
    
    # Log incoming request
    logger.info({
        'event': 'request_received',
        'method': http_method,
        'path': path,
        'request_id': request_id
    })
    
    # CORS headers (allow all origins for public API)
    headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With, X-Encoding, X-State, X-Model, X-Origin',
        'X-Request-ID': request_id
    }
    
    # Handle OPTIONS for CORS preflight
    if http_method == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': headers,
            'body': ''
        }
    
    try:
        # Route to appropriate handler
        if path == '/api/connection' and http_method == 'GET':
            return handle_connection(headers, request_id)
        
        elif path == '/api/s3-upload-url' and http_method == 'POST':
            body = parse_json_body(event.get('body', '{}'))
            return handle_s3_upload_url(body, headers, request_id)
        
        elif path == '/api/s3-view-url' and http_method == 'POST':
            body = parse_json_body(event.get('body', '{}'))
            return handle_s3_view_url(body, headers, request_id)

        elif path.startswith('/api/vitallens'):
            return handle_vitallens_proxy(event, headers, request_id)
        
        else:
            logger.warning({
                'event': 'route_not_found',
                'path': path,
                'method': http_method,
                'request_id': request_id
            })
            return {
                'statusCode': 404,
                'headers': headers,
                'body': json.dumps({
                    'error': 'Not found',
                    'path': path,
                    'request_id': request_id
                })
            }
    
    except json.JSONDecodeError as e:
        logger.error({
            'event': 'json_decode_error',
            'error': str(e),
            'request_id': request_id
        })
        return {
            'statusCode': 400,
            'headers': headers,
            'body': json.dumps({
                'error': 'Invalid JSON in request body',
                'request_id': request_id
            })
        }
    
    except ValueError as e:
        logger.error({
            'event': 'validation_error',
            'error': str(e),
            'request_id': request_id
        })
        return {
            'statusCode': 400,
            'headers': headers,
            'body': json.dumps({
                'error': str(e),
                'request_id': request_id
            })
        }
    
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        logger.error({
            'event': 'aws_client_error',
            'error_code': error_code,
            'error': str(e),
            'request_id': request_id
        })
        return {
            'statusCode': 503,
            'headers': headers,
            'body': json.dumps({
                'error': 'AWS service error',
                'code': error_code,
                'request_id': request_id
            })
        }
    
    except Exception as e:
        logger.error({
            'event': 'unexpected_error',
            'error': str(e),
            'error_type': type(e).__name__,
            'request_id': request_id
        }, exc_info=True)
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({
                'error': 'Internal server error',
                'request_id': request_id
            })
        }


def parse_json_body(body_str):
    # Parse JSON body.
    if not body_str:
        return {}
    return json.loads(body_str)


def handle_connection(headers, request_id):
    # Generate presigned WebSocket URL for AgentCore.
    # Validate configuration
    if not AGENTCORE_RUNTIME_ARN:
        logger.error({
            'event': 'missing_config',
            'missing': 'AGENTCORE_RUNTIME_ARN',
            'request_id': request_id
        })
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({
                'error': 'AgentCore runtime not configured',
                'request_id': request_id
            })
        }
    
    try:
        # Generate presigned WebSocket URL using botocore's SigV4QueryAuth
        from botocore.auth import SigV4QueryAuth
        from botocore.awsrequest import AWSRequest
        from urllib.parse import urlparse
        import boto3
        
        session = boto3.Session()
        credentials = session.get_credentials()
        
        if not credentials:
            raise ValueError("Unable to retrieve AWS credentials")
        
        # Build the WebSocket URL (use https for signing, then convert to wss)
        https_url = f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com/runtimes/{AGENTCORE_RUNTIME_ARN}/ws?qualifier={QUALIFIER}"
        parsed_url = urlparse(https_url)
        
        # Create request for signing
        request = AWSRequest(
            method='GET',
            url=https_url,
            headers={'Host': parsed_url.netloc}
        )
        
        # Sign the request with SigV4QueryAuth (adds signature to query string)
        # URL valid for 1 hour
        SigV4QueryAuth(credentials, 'bedrock-agentcore', AWS_REGION, expires=3600).add_auth(request)
        
        # Convert back to wss://
        presigned_url = request.url.replace("https://", "wss://")
        
        logger.info({
            'event': 'websocket_url_generated',
            'runtime_arn': AGENTCORE_RUNTIME_ARN,
            'request_id': request_id
        })
        
        response_data = {
            'websocket_url': presigned_url,
            'status': 'ok',
            'expires_in': 3600  # 1 hour
        }
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps(response_data)
        }
    
    except Exception as e:
        logger.error({
            'event': 'websocket_url_generation_failed',
            'error': str(e),
            'request_id': request_id
        }, exc_info=True)
        raise


def handle_s3_upload_url(body, headers, request_id):
    # Generate presigned URL for S3 upload.
    # Validate input
    filename = body.get('filename', '').strip()
    if not filename:
        raise ValueError("filename is required")
    
    # Sanitize filename (prevent path traversal)
    filename = os.path.basename(filename)
    if not filename or filename.startswith('.'):
        raise ValueError("Invalid filename")
    
    content_type = body.get('contentType', 'application/octet-stream')
    
    # Validate content type (basic check)
    allowed_types = [
        'image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp',
        'video/mp4', 'video/quicktime', 'video/webm',
        'application/pdf', 'application/octet-stream'
    ]
    if content_type not in allowed_types:
        logger.warning({
            'event': 'unusual_content_type',
            'content_type': content_type,
            'filename': filename,
            'request_id': request_id
        })
    
    try:
        # Generate unique S3 key with timestamp
        timestamp = secrets.token_hex(8)
        s3_key = f"uploads/{timestamp}_{filename}"
        
        # Generate presigned URL for PUT with 5 minute expiry
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': S3_BUCKET,
                'Key': s3_key,
                'ContentType': content_type,
                'ServerSideEncryption': 'AES256'  # Encrypt at rest
            },
            ExpiresIn=300  # 5 minutes
        )
        
        # Generate the final S3 URLs
        s3_url = f"https://{S3_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{s3_key}"
        s3_uri = f"s3://{S3_BUCKET}/{s3_key}"
        
        logger.info({
            'event': 's3_upload_url_generated',
            'filename': filename,
            's3_key': s3_key,
            'content_type': content_type,
            'request_id': request_id
        })
        
        response_data = {
            'status': 'ok',
            'uploadUrl': presigned_url,
            's3Url': s3_url,
            's3Uri': s3_uri,
            'key': s3_key,
            'expires_in': 300  # 5 minutes
        }
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps(response_data)
        }
    
    except ClientError as e:
        logger.error({
            'event': 's3_upload_url_generation_failed',
            'error': str(e),
            'filename': filename,
            'request_id': request_id
        })
        raise


def handle_s3_view_url(body, headers, request_id):
    # Generate presigned URL for viewing S3 object.
    s3_uri = body.get('s3Uri', '').strip()
    
    # Validate S3 URI format
    if not s3_uri:
        raise ValueError("s3Uri is required")
    
    if not s3_uri.startswith('s3://'):
        raise ValueError("Invalid S3 URI - must start with s3://")
    
    # Parse S3 URI: s3://bucket/key
    parts = s3_uri[5:].split('/', 1)
    if len(parts) != 2:
        raise ValueError("Invalid S3 URI format - expected s3://bucket/key")
    
    bucket_name, s3_key = parts
    
    # Validate bucket name (basic check)
    if not bucket_name or not s3_key:
        raise ValueError("Invalid S3 URI - bucket or key is empty")
    
    try:
        # Verify object exists before generating URL (optional but good practice)
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.warning({
                    'event': 's3_object_not_found',
                    's3_uri': s3_uri,
                    'request_id': request_id
                })
                # Still generate URL - object might be uploaded soon
            else:
                raise
        
        # Generate presigned URL for GET (7 days expiration)
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket_name,
                'Key': s3_key
            },
            ExpiresIn=604800  # 7 days
        )
        
        logger.info({
            'event': 's3_view_url_generated',
            's3_uri': s3_uri,
            'bucket': bucket_name,
            'key': s3_key,
            'request_id': request_id
        })
        
        response_data = {
            'status': 'ok',
            'viewUrl': presigned_url,
            'expires_in': 604800  # 7 days
        }
        
        return {
            'statusCode': 200,
            'headers': headers,
            'body': json.dumps(response_data)
        }
    
    except ClientError as e:
        logger.error({
            'event': 's3_view_url_generation_failed',
            'error': str(e),
            's3_uri': s3_uri,
            'request_id': request_id
        })
        raise


def _get_header(headers, name):
    for key, value in (headers or {}).items():
        if key.lower() == name:
            return value
    return None


def handle_vitallens_proxy(event, headers, request_id):
    if not VITALLENS_API_KEY:
        return {
            'statusCode': 500,
            'headers': headers,
            'body': json.dumps({
                'error': 'VitalLens API key not configured',
                'request_id': request_id
            })
        }

    path = event.get('path') or event.get('rawPath', '/')
    target_path = path.replace('/api/vitallens', '', 1)
    if target_path == '':
        target_path = '/'

    query = event.get('rawQueryString')
    if not query:
        params = event.get('queryStringParameters') or {}
        query = urllib.parse.urlencode(params) if params else ''

    url = f"{VITALLENS_API_BASE}{target_path}"
    if query:
        url = f"{url}?{query}"

    method = (event.get('httpMethod')
              or event.get('requestContext', {}).get('http', {}).get('method', 'GET'))

    incoming_headers = event.get('headers') or {}
    upstream_headers = {
        'x-api-key': VITALLENS_API_KEY
    }

    content_type = _get_header(incoming_headers, 'content-type')
    if content_type:
        upstream_headers['Content-Type'] = content_type

    for header_name in ['x-encoding', 'x-state', 'x-model', 'x-origin']:
        header_value = _get_header(incoming_headers, header_name)
        if header_value:
            upstream_headers[header_name] = header_value

    body = event.get('body', '') or ''
    is_b64 = event.get('isBase64Encoded', False)
    body_bytes = None
    if method != 'GET':
        if is_b64:
            body_bytes = base64.b64decode(body)
        else:
            body_bytes = body.encode('utf-8')

    req = urllib.request.Request(url, data=body_bytes, headers=upstream_headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            resp_body = resp.read()
            status_code = resp.status
            content_type = resp.headers.get('Content-Type', 'application/json')
    except HTTPError as e:
        resp_body = e.read()
        status_code = e.code
        content_type = e.headers.get('Content-Type', 'application/json')
    except URLError as e:
        logger.error({
            'event': 'vitallens_proxy_error',
            'error': str(e),
            'request_id': request_id
        })
        return {
            'statusCode': 502,
            'headers': headers,
            'body': json.dumps({
                'error': 'VitalLens proxy request failed',
                'request_id': request_id
            })
        }

    try:
        body_text = resp_body.decode('utf-8')
        encoded = False
    except UnicodeDecodeError:
        body_text = base64.b64encode(resp_body).decode('ascii')
        encoded = True

    out_headers = headers.copy()
    out_headers['Content-Type'] = content_type

    return {
        'statusCode': status_code,
        'headers': out_headers,
        'body': body_text,
        'isBase64Encoded': encoded
    }
