import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import OptionalUser, OwnerScope, SessionDep
from app.api.v1.auth import token_response
from app.schemas.auth import (
    AcceptInvitationRequest,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    InvitationPreview,
    TokenResponse,
)
from app.services import auth_service, invitation_service

router = APIRouter(prefix="/invitations", tags=["invitations"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    data: InvitationCreate, scope: OwnerScope, session: SessionDep
) -> InvitationCreated:
    invitation, token = await invitation_service.create_invitation(session, scope, data)
    return InvitationCreated(
        **InvitationOut.model_validate(invitation).model_dump(),
        url=invitation_service.invitation_url(token),
    )


@router.get("")
async def list_invitations(scope: OwnerScope, session: SessionDep) -> list[InvitationOut]:
    invitations = await invitation_service.list_invitations(session, scope)
    return [InvitationOut.model_validate(i) for i in invitations]


@router.delete("/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invitation_id: uuid.UUID, scope: OwnerScope, session: SessionDep
) -> None:
    await invitation_service.revoke_invitation(session, scope, invitation_id)


@router.get("/by-token/{token}")
async def preview_invitation(token: str, session: SessionDep) -> InvitationPreview:
    return await invitation_service.preview_invitation(session, token)


@router.post("/by-token/{token}/accept")
async def accept_invitation(
    token: str,
    data: AcceptInvitationRequest,
    session: SessionDep,
    response: Response,
    user: OptionalUser,
) -> TokenResponse:
    accepted_by = await invitation_service.accept_invitation(session, token, data, user)
    return token_response(response, auth_service.issue_tokens(accepted_by.id))
