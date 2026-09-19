"""Strict input schemas; money uses integer USD cents, never floats."""
from typing import Literal
from pydantic import BaseModel, Field, EmailStr, ConfigDict, ValidationInfo, field_validator, model_validator
from .config import US_ZONES

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    @field_validator('*', mode='before')
    @classmethod
    def safe_text(cls, value, info: ValidationInfo):
        if isinstance(value, str):
            if '\x00' in value or '\r' in value:
                raise ValueError('Control characters are not allowed.')
            if info.field_name in ('name','business','customer_name','city','tagline','address','phone','email','vehicle_notes','signer_name') and '\n' in value:
                raise ValueError('Use a single line for this field.')
        return value

class Register(Model):
    name: str = Field(min_length=2,max_length=80)
    email: EmailStr
    password: str = Field(min_length=12,max_length=128)
    business: str = Field(min_length=2,max_length=80)
    slug: str = Field(min_length=3,max_length=45,pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    timezone: str = 'America/New_York'
    accepted_terms: Literal[True]
    plan_id: Literal['solo'] = 'solo'
    website: str = Field(default='', max_length=200)
    @field_validator('timezone')
    @classmethod
    def zone(cls,v):
        if v not in US_ZONES: raise ValueError('Choose a supported US timezone.')
        return v

class Login(Model):
    email: EmailStr
    password: str = Field(min_length=1,max_length=128)
class EmailInput(Model): email: EmailStr
class PasswordReset(Model):
    token: str = Field(min_length=20,max_length=128)
    password: str = Field(min_length=12,max_length=128)
class PasswordChange(Model):
    current_password: str = Field(max_length=128)
    password: str = Field(min_length=12,max_length=128)

class VehicleRate(Model):
    price: int = Field(ge=100, le=1500000, strict=True)
    minutes: int = Field(ge=30, le=720, strict=True)

class Package(Model):
    id: str = Field(pattern=r'^[a-z0-9-]{1,40}$')
    name: str = Field(min_length=2,max_length=60)
    description: str = Field(max_length=300)
    price: int = Field(ge=100,le=1000000,strict=True)
    minutes: int = Field(ge=30,le=600,strict=True)
    active: bool = True
    vehicle_rates: dict[str, VehicleRate] = Field(default_factory=dict, max_length=8)
class Extra(Model):
    id: str = Field(pattern=r'^[a-z0-9-]{1,40}$')
    name: str = Field(min_length=2,max_length=60)
    price: int = Field(ge=0,le=500000,strict=True)
    minutes: int = Field(ge=0,le=240,strict=True)
    active: bool = True
class Vehicle(Model):
    id: str = Field(pattern=r'^[a-z0-9-]{1,40}$')
    name: str = Field(min_length=2,max_length=40)
    price: int = Field(ge=0,le=500000,strict=True)
    minutes: int = Field(ge=0,le=180,strict=True)
class Hours(Model):
    enabled: bool
    start: str = Field(pattern=r'^([01][0-9]|2[0-3]):[0-5][0-9]$')
    end: str = Field(pattern=r'^([01][0-9]|2[0-3]):[0-5][0-9]$')
    @model_validator(mode='after')
    def ordered(self):
        if self.enabled and self.end <= self.start: raise ValueError('Closing time must follow opening time.')
        return self
class Settings(Model):
    revision: int | None = Field(default=None, ge=0, strict=True)
    name: str = Field(min_length=2,max_length=80)
    tagline: str = Field(max_length=180)
    city: str = Field(max_length=80)
    phone: str = Field(max_length=25)
    contact_email: EmailStr
    timezone: str
    zip_codes: list[str] = Field(min_length=1,max_length=300)
    packages: list[Package] = Field(min_length=1,max_length=12)
    extras: list[Extra] = Field(max_length=15)
    vehicles: list[Vehicle] = Field(min_length=1,max_length=8)
    hours: list[Hours] = Field(min_length=7,max_length=7)
    buffer_minutes: int = Field(ge=0,le=180,strict=True)
    lead_hours: int = Field(ge=1,le=168,strict=True)
    horizon_days: int = Field(ge=7,le=90,strict=True)
    payment_method: Literal['in_person','square'] = 'in_person'
    deposit_percent: int = Field(ge=0,le=100,strict=True)
    tax_basis_points: int = Field(ge=0,le=2000,strict=True)
    cancellation_hours: int = Field(ge=0,le=168,strict=True)
    cancellation_policy: str = Field(min_length=20,max_length=2000)
    requires_water: bool
    requires_power: bool
    policy_reviewed: bool = False
    prices_reviewed: bool = False
    @model_validator(mode='after')
    def valid_vehicle_rates(self):
        vehicles = {v.id for v in self.vehicles}
        if not any(p.active for p in self.packages):
            raise ValueError('Keep at least one active package.')
        for package in self.packages:
            if set(package.vehicle_rates) - vehicles:
                raise ValueError('A service rate refers to a vehicle category that does not exist.')
            for vehicle in self.vehicles:
                rate = package.vehicle_rates.get(vehicle.id)
                minutes = rate.minutes if rate else package.minutes + vehicle.minutes
                if minutes + self.buffer_minutes > 720:
                    raise ValueError('A service and travel buffer must fit within 12 hours. Use a custom quote for longer jobs.')
        return self
    @field_validator('timezone')
    @classmethod
    def timezone_allowed(cls,v):
        if v not in US_ZONES: raise ValueError('Choose a supported US timezone.')
        return v
    @field_validator('zip_codes')
    @classmethod
    def valid_zip(cls,v):
        from .postal import validate_zip
        for z in v: validate_zip(z)
        return list(dict.fromkeys(v))
    @model_validator(mode='after')
    def unique_ids(self):
        for items in [self.packages,self.extras,self.vehicles]:
            if len(set(x.id for x in items)) != len(items): raise ValueError('Item IDs must be unique.')
        if not any(p.active for p in self.packages): raise ValueError('Keep at least one package active.')
        if not any(h.enabled for h in self.hours): raise ValueError('Open at least one day.')
        return self

class Selection(Model):
    package_id: str = Field(max_length=40)
    vehicle_id: str = Field(max_length=40)
    extra_ids: list[str] = Field(default_factory=list,max_length=15)
    zip: str = Field(pattern=r'^[0-9]{5}$')
    @field_validator('zip')
    @classmethod
    def us_zip(cls,v):
        from .postal import validate_zip
        return validate_zip(v)
    condition: Literal['standard','heavy'] = 'standard'
    quote_token: str | None = Field(default=None,max_length=128)
    @field_validator('extra_ids')
    @classmethod
    def unique_extras(cls,v):
        if len(set(v)) != len(v): raise ValueError('Duplicate add-ons are not allowed.')
        return v
class Customer(Model):
    country: Literal['US'] = 'US'
    state: str = Field(default='',max_length=2,pattern=r'^(?:[A-Z]{2})?$')
    customer_name: str = Field(min_length=2,max_length=80)
    email: EmailStr
    phone: str = Field(min_length=10,max_length=25,pattern=r'^\+?[0-9 ().-]+$')
    address: str = Field(min_length=8,max_length=220)
    vehicle_notes: str = Field(default='',max_length=160)
    notes: str = Field(default='',max_length=1500)
class USAddress(Selection, Customer):
    @model_validator(mode='after')
    def validate_us_address(self):
        from .postal import zip_states
        states=zip_states(self.zip)
        if self.state and self.state not in states:
            raise ValueError('The state does not match this US ZIP code.')
        if not self.state: self.state=sorted(states)[0]
        return self

class BookingInput(USAddress):
    start_ts: int = Field(gt=0,strict=True)
    idempotency_key: str = Field(pattern=r'^[a-zA-Z0-9-]{20,64}$')
    accepted_policy: Literal[True]
    has_water: bool = False
    has_power: bool = False
    website: str = Field(default='',max_length=200)
class QuoteRequest(USAddress):
    website: str = Field(default='',max_length=200)
class QuoteOffer(Model):
    amount: int = Field(ge=100,le=1000000,strict=True)
    minutes: int = Field(ge=30,le=600,strict=True)
    message: str = Field(min_length=5,max_length=1000)
class SlotRequest(Selection):
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
class Block(Model):
    start_ts: int = Field(gt=0,strict=True)
    end_ts: int = Field(gt=0,strict=True)
    label: str = Field(min_length=2,max_length=80)
    @model_validator(mode='after')
    def ordered(self):
        if not 0 < self.end_ts-self.start_ts <= 90*86400: raise ValueError('Choose a valid blocked period of up to 90 days.')
        return self
class Reschedule(Model): start_ts: int = Field(gt=0,strict=True)
class Action(Model): action: Literal['complete','cancel','no_show']
class Refund(Model): confirmation: Literal['REFUND DEPOSIT']
class DeleteAccount(Model):
    password: str = Field(max_length=128)
    confirmation: Literal['DELETE MY ACCOUNT']

def default_settings(name,email,timezone='America/New_York'):
    return Settings(name=name,tagline='A better detail. Right at your door.',city='Your city, USA',phone='',contact_email=email,
        timezone=timezone,zip_codes=['10001'],
        packages=[
            Package(id='refresh',name='The refresh',description='Exterior hand wash, wheels, interior vacuum and wipe-down.',price=9900,minutes=90),
            Package(id='full-detail',name='The full detail',description='A thorough interior and exterior clean, finished with paint protection.',price=18900,minutes=180),
            Package(id='interior',name='Interior reset',description='Deep interior cleaning, upholstery treatment and attention to every surface.',price=14900,minutes=150)],
        extras=[Extra(id='pet-hair',name='Pet hair removal',price=3500,minutes=30),Extra(id='seat-shampoo',name='Seat shampoo',price=4500,minutes=45),Extra(id='wax',name='Hand wax protection',price=4000,minutes=30)],
        vehicles=[Vehicle(id='sedan',name='Sedan / coupe',price=0,minutes=0),Vehicle(id='suv',name='SUV / crossover',price=2500,minutes=30),Vehicle(id='truck',name='Truck / large SUV',price=4500,minutes=45)],
        hours=[Hours(enabled=i<6,start='08:00',end='18:00') for i in range(7)],buffer_minutes=30,lead_hours=2,horizon_days=45,
        deposit_percent=20,tax_basis_points=0,cancellation_hours=24,
        cancellation_policy='Please give at least 24 hours notice to cancel or reschedule. Deposit refunds are reviewed by the business under its published policy. Weather changes can be rescheduled. Contact the business for assistance.',
        requires_water=False,requires_power=False,policy_reviewed=False).model_dump()
