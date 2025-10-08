from typing import cast

from fastapi import APIRouter, status, Depends, HTTPException
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_jwt_auth_manager, get_s3_storage_client
from exceptions import TokenExpiredError, InvalidTokenError, S3FileUploadError
from schemas.profiles import ProfileResponseSchema, ProfileRequestSchema
from database import get_db, UserGroupModel, UserModel, UserProfileModel, UserGroupEnum
from security.http import get_token
from security.interfaces import JWTAuthManagerInterface
from storages import S3StorageInterface

router = APIRouter()


@router.post(
    path="/users/{user_id}/profile/",
    response_model=ProfileResponseSchema,
    status_code=status.HTTP_201_CREATED
)
async def create_profile(
        user_id: int,
        token: str = Depends(get_token),
        request_data: ProfileRequestSchema = Depends(ProfileRequestSchema.as_form),
        jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
        s3_client: S3StorageInterface = Depends(get_s3_storage_client),
        db: AsyncSession = Depends(get_db)
):
    try:
        payload = jwt_manager.decode_access_token(token)
        token_user_id = payload.get("user_id")
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired."
        )
    except InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token."
        )
    if user_id != token_user_id:
        result_user_group = await db.execute(
            select(UserGroupModel)
            .join(UserModel)
            .where(UserModel.id == token_user_id)
        )
        user_group = result_user_group.scalar_one_or_none()
        if not user_group or user_group.name == UserGroupEnum.USER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to edit this profile."
            )

    result_user = await db.execute(
        select(UserModel)
        .where(UserModel.id == user_id)
    )
    user = result_user.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or not active."
        )

    result_user_profile = await db.execute(
        select(UserProfileModel)
        .where(UserProfileModel.user_id == user_id)
    )
    user_profile = result_user_profile.scalar_one_or_none()
    if user_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already has a profile."
        )

    avatar_file_name = f"avatars/{user.id}_{request_data.avatar.filename}"
    avatar_bytes = await request_data.avatar.read()
    try:
        await s3_client.upload_file(file_name=avatar_file_name, file_data=avatar_bytes)
        avatar_url = await s3_client.get_file_url(avatar_file_name)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload avatar. Please try again later."
        )

    new_profile = UserProfileModel(
        first_name=request_data.first_name,
        last_name=request_data.last_name,
        avatar=avatar_file_name,
        gender=request_data.gender,
        date_of_birth=request_data.date_of_birth,
        info=request_data.info,
        user_id=user_id
    )
    db.add(new_profile)
    await db.commit()
    await db.refresh(new_profile)

    return ProfileResponseSchema(
        id=new_profile.id,
        user_id=new_profile.user_id,
        first_name=new_profile.first_name,
        last_name=new_profile.last_name,
        gender=new_profile.gender,
        date_of_birth=new_profile.date_of_birth,
        info=new_profile.info,
        avatar=cast(HttpUrl, avatar_url)
    )
