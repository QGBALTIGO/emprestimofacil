"""Import existing signed, disbursed loan contracts; never turn an application into a debt."""
from datetime import date,datetime,timezone
from typing import Literal
from uuid import UUID

from pydantic import Field,StrictInt,StrictStr,model_validator

from backend.app import Applicant,Model


class Installment(Model):
    number: StrictInt = Field(ge=1,le=12)
    amount_cents: StrictInt = Field(ge=100,le=100000000)
    due_date: date
    offer_id: StrictStr = Field(pattern=r'^[A-Za-z0-9-]{1,80}$')
    product_id: UUID


class Contract(Model):
    id: UUID
    reference: StrictStr = Field(min_length=3,max_length=100)
    customer: Applicant
    principal_cents: StrictInt = Field(ge=50000,le=350000)
    signed: Literal[True]
    signed_at: datetime
    signed_document_sha256: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')
    funds_disbursed: Literal[True]
    disbursed_at: datetime
    disbursement_reference: StrictStr = Field(min_length=16,max_length=150)
    payment_data_authorized: Literal[True]
    frequency: Literal['weekly']
    installments: list[Installment] = Field(min_length=3,max_length=12)

    @model_validator(mode='after')
    def consistent(self):
        now=datetime.now(timezone.utc)
        if (self.signed_at.tzinfo is None or self.disbursed_at.tzinfo is None
                or not self.signed_at<=self.disbursed_at<=now):
            raise ValueError('Signing and disbursement must have already occurred.')
        if len(self.installments) not in {3,6,9,12}:raise ValueError('Weekly term')
        if [i.number for i in self.installments]!=list(range(1,len(self.installments)+1)):raise ValueError('Sequence')
        dates=[i.due_date for i in self.installments]
        if dates[0]<=self.disbursed_at.date() or any((b-a).days!=7 for a,b in zip(dates,dates[1:])):
            raise ValueError('Only installments after disbursement with a weekly schedule.')
        if sum(i.amount_cents for i in self.installments)<self.principal_cents:raise ValueError('Total below principal')
        return self


class Invite(Model):
    token: StrictStr = Field(min_length=30,max_length=100)
