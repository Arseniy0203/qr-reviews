from fastapi import APIRouter

from app.api.v1 import auth, branches, categories, employees, invitations, organization, qr_points

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(organization.router)
api_router.include_router(invitations.router)
api_router.include_router(branches.router)
api_router.include_router(employees.router)
api_router.include_router(categories.router)
api_router.include_router(qr_points.router)
