from drf_yasg import openapi

api_info = openapi.Info(
    "Weel API",
    "v1",
    "API documentation for the Weel backend",
    contact=openapi.Contact(name="Weel Support", url="https://weel.uz"),
    license=openapi.License(name="Proprietary"),
)
