from fastapi import APIRouter, Depends, Request

from app.auth import verify_api_key
from app.formats import dimensions
from app.schemas import BundleInfo, FormatInfo, TemplateInfo, TemplatesResponse

router = APIRouter(prefix="/v1", tags=["templates"], dependencies=[Depends(verify_api_key)])


@router.get("/templates", response_model=TemplatesResponse)
async def list_templates(request: Request) -> TemplatesResponse:
    registry = request.app.state.templates

    templates = []
    for spec in registry.specs:
        formats = []
        for fmt in spec.formats:
            width, height = dimensions(fmt)
            formats.append(FormatInfo(format=fmt, width=width, height=height))
        templates.append(
            TemplateInfo(
                id=spec.id,
                bundle=spec.bundle,
                name=spec.name,
                description=spec.description,
                formats=formats,
                fields=list(spec.fields),
                required=list(spec.required),
                defaults=dict(spec.defaults),
                limits=dict(spec.limits),
                accepts_image=spec.accepts_image,
                accepts_icon=spec.accepts_icon,
            )
        )

    bundles = [
        BundleInfo(name=bundle.name, description=bundle.description) for bundle in registry.bundles
    ]
    return TemplatesResponse(bundles=bundles, templates=templates)
