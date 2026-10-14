import re
from datetime import date, datetime, timezone
from typing import Literal, Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, StringConstraints, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


def cpf_valid(value):
    from backend.app import valid_cpf
    return valid_cpf(value)


def email_valid(value):
    return len(value) <= 254 and bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value))


def https_valid(value, instagram=False):
    try:
        parsed = urlparse(value)
        return bool(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
                    and len(value) <= 500 and (not instagram or parsed.hostname in {'instagram.com', 'www.instagram.com'}
                                              and parsed.path not in {'', '/'}))
    except ValueError:
        return False


class Profile(Model):
    full_name: StrictStr = Field(min_length=3, max_length=150)
    email: StrictStr
    phone: StrictStr
    cpf: StrictStr | None = None

    @model_validator(mode='after')
    def valid(self):
        if len(self.full_name.split()) < 2 or not all(c.isalpha() or c in " '-’." for c in self.full_name):
            raise ValueError('Name')
        if not email_valid(self.email) or not re.fullmatch(r'[1-9]\d9\d{8}', self.phone):
            raise ValueError('Contact')
        if self.cpf is not None and not cpf_valid(self.cpf):
            raise ValueError('CPF')
        self.email = self.email.lower()
        return self


class CodeRequest(Model):
    channel: Literal['email', 'sms']


class CodeCheck(CodeRequest):
    code: StrictStr = Field(pattern=r'^[0-9]{6}$', repr=False)


class BankConsent(Model):
    consent: Literal[True]


class AdminLogin(Model):
    username: StrictStr = Field(min_length=3, max_length=80)
    password: Annotated[StrictStr, StringConstraints(strip_whitespace=False)] = Field(min_length=12, max_length=128, repr=False)
    code: StrictStr = Field(pattern=r'^[0-9]{6}$', repr=False)


class Account(Model):
    trade_name: StrictStr = Field(min_length=1, max_length=150)
    monthly_income: float = Field(strict=True, ge=1200, le=100000000, allow_inf_nan=False)
    occupation: Literal['employed', 'self_employed', 'driver', 'business_owner', 'retired', 'other']
    income_source: StrictStr | None = Field(default=None, max_length=200)
    activity: StrictStr = Field(min_length=1, max_length=200)
    support_email: StrictStr = Field(default='', max_length=254)
    support_phone: StrictStr = Field(default='', max_length=22)
    support_site: StrictStr = Field(default='', max_length=500)

    @model_validator(mode='after')
    def valid(self):
        if self.occupation == 'other' and (not self.income_source or len(self.income_source) < 3):
            raise ValueError('Income source')
        if not any([self.support_email, self.support_phone, self.support_site]):
            raise ValueError('Support')
        if self.support_email and not email_valid(self.support_email):
            raise ValueError('Email')
        if self.support_phone and not re.fullmatch(r'[1-9]\d{9,12}', re.sub(r'\D', '', self.support_phone)):
            raise ValueError('Phone')
        if self.support_site and not https_valid(self.support_site):
            raise ValueError('Website')
        return self


class Holder(Model):
    gender: Literal['', 'female', 'male', 'other', 'prefer_not'] = ''
    document_type: Literal['rg', 'cnh']
    document_number: StrictStr = Field(min_length=1, max_length=30)
    issue_date: date


class FamilyContact(Model):
    name: StrictStr = Field(min_length=3, max_length=150)
    phone: StrictStr = Field(pattern=r'^[1-9]\d{9,12}$')
    relation: Literal['father', 'mother', 'sibling', 'spouse', 'other']


class Location(Model):
    latitude: float = Field(strict=True, ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(strict=True, ge=-180, le=180, allow_inf_nan=False)
    accuracy: float = Field(strict=True, ge=0, le=10000000, allow_inf_nan=False)
    captured_at: datetime

    @field_validator('captured_at')
    @classmethod
    def valid(cls, value):
        if value.tzinfo is None or abs((datetime.now(timezone.utc) - value).total_seconds()) > 86400:
            raise ValueError('Capture date')
        return value


class PFConsents(Model):
    data: Literal[True]
    contacts: Literal[True]
    location: Literal[True]
    address_captures: Literal[True]
    version: Literal['2026-10-13']


class PF(Model):
    schema_version: Literal[1]
    applicant: dict
    account: Account
    address: dict
    holder: Holder
    contacts: list[FamilyContact] = Field(min_length=5, max_length=5)
    instagram: StrictStr
    location: Location
    address_proof_date: date
    consents: PFConsents

    @model_validator(mode='after')
    def valid(self):
        from backend.app import Applicant, Address
        self.applicant = Applicant.model_validate(self.applicant).model_dump()
        self.address = Address.model_validate(self.address).model_dump()
        if len({c.phone for c in self.contacts}) != 5 or not https_valid(self.instagram, True):
            raise ValueError('Contacts or Instagram')
        birth = date.fromisoformat(self.applicant['birth_date'])
        if not birth <= self.holder.issue_date <= date.today():
            raise ValueError('Document date')
        if not 0 <= (date.today() - self.address_proof_date).days <= 31:
            raise ValueError('Address proof date')
        return self

    def required_files(self):
        result = ['house_front', 'house_entry', 'front', 'back', 'selfie', 'video', 'address_proof', 'instagram_proof']
        if self.account.occupation == 'employed':
            result.append('work_proof')
        if self.account.occupation == 'driver':
            result.append('driver_proof')
        return result
