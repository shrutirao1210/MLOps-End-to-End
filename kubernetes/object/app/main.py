import logging
from fastapi import FastAPI, File, UploadFile, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from prometheus_fastapi_instrumentator import Instrumentator

from . import model_loader, schemas # Use relative imports within the package

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Lifespan Management (for loading model on startup) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the ML model
    logger.info("Application startup: Loading Object Detection model...")
    try:
        model_loader.load_model()
        logger.info("Object Detection Model loaded successfully during startup.")
    except Exception as e:
        logger.error(f"Fatal Error: Failed to load Object Detection model during startup: {e}", exc_info=True)
        # Allow app to start, but endpoints needing the model will fail.
    yield
    # Clean up resources if needed
    logger.info("Application shutdown: Cleaning up resources...")

API_PREFIX = "/api"

# --- FastAPI App Initialization ---
app = FastAPI(
    title="Object Detection API",
    description="API to detect objects in uploaded images.",
    version="0.1.0",
    root_path=API_PREFIX,
    lifespan=lifespan
)

# Configure Prometheus instrumentator
instrumentator = Instrumentator()
instrumentator.instrument(app)
instrumentator.expose(app, include_in_schema=True, should_gzip=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API Endpoints ---
@app.get("/health", response_model=schemas.HealthCheckResponse, tags=["Health"])
async def health_check():
    """Performs a basic health check, including model status."""
    model_ready = bool(model_loader.model)
    status_msg = "ready" if model_ready else "model_loading_failed_or_in_progress"
    logger.info(f"Health check requested. Model status: {status_msg}")
    return schemas.HealthCheckResponse(status=status_msg)

@app.post("/object", response_model=schemas.ObjectDetectionResponse, tags=["Detection"])
async def detect_objects_endpoint(file: UploadFile = File(...)):
    """
    Uploads an image file and returns detected objects with bounding boxes.

    - **file**: The image file to upload (e.g., JPEG, PNG).
    """
    logger.info(f"Received request for object detection: {file.filename}")
    if not file.content_type or not file.content_type.startswith("image/"):
        logger.warning(f"Invalid file type received: {file.content_type}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Please upload an image.",
        )

    if not model_loader.model:
        logger.error("Detection request failed: Model is not loaded.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model is not ready or failed to load. Please try again later.",
        )

    try:
        image_bytes = await file.read()
        logger.debug(f"Read {len(image_bytes)} bytes from uploaded file: {file.filename}")

        detected_objects_data = model_loader.detect_objects(image_bytes)
        detected_objects = [schemas.DetectedObject(**obj_data) for obj_data in detected_objects_data]

        logger.info(f"Successfully processed object detection for {file.filename}")
        return schemas.ObjectDetectionResponse(
            filename=file.filename,
            objects=detected_objects
        )

    except ValueError as ve:
         logger.error(f"Value error during detection for {file.filename}: {ve}", exc_info=True)
         return schemas.ObjectDetectionResponse(filename=file.filename, objects=[], error=f"Detection error: {ve}")
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Unexpected error during detection for {file.filename}: {e}", exc_info=True)
        return schemas.ObjectDetectionResponse(
            filename=file.filename,
            objects=[],
            error="An unexpected error occurred during object detection."
        )
    finally:
         await file.close()
         logger.debug(f"Closed file handle for: {file.filename}")

# To run locally (for development): uvicorn object_detection.app.main:app --reload --port 8001
# Note: Use a different port (e.g., 8001) if caption service runs on 8000