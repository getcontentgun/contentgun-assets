from fastapi import APIRouter

from app.auth import CurrentTenant
from app.formats import dimensions
from app.schemas import BundleInfo, FormatInfo, TemplateInfo, TemplatesResponse

router = APIRouter(prefix="/v1", tags=["templates"])


@router.get("/templates", response_model=TemplatesResponse)
async def list_templates(tenant: CurrentTenant) -> TemplatesResponse:
    registry = tenant.registry

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
                accepts_code=spec.accepts_code,
                icons=sorted(registry.icons(spec)),
            )
        )

    bundles = [
        BundleInfo(name=bundle.name, description=bundle.description) for bundle in registry.bundles
    ]
    return TemplatesResponse(bundles=bundles, templates=templates)
