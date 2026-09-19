"""Focused setup checks and pricing/billing routes. No secrets in responses."""
from fastapi import Depends, HTTPException
from pydantic import Field
from . import config, plans, core, models
from .db import db

class ConfirmChange(models.Model):
    token: str = Field(min_length=20,max_length=128)

def install(app,owner,session,login_session,ip):
    from .google_auth import install as install_google
    install_google(app,session,login_session,ip)

    @app.get('/api/billing/plans')
    def billing_plans(shop=Depends(owner)):
        return {'plans':plans.catalog(),'selected':plans.selected(shop),'features':plans.features(shop)}

    @app.post('/api/billing/change/preview')
    def preview(body:plans.PlanSelection,shop=Depends(owner)):
        from .billing import preview_change
        return preview_change(shop,body.plan_id)

    @app.post('/api/billing/change/confirm')
    def confirm(body:ConfirmChange,shop=Depends(owner)):
        from .billing import confirm_change
        return confirm_change(shop,body.token)

    @app.get('/api/setup')
    def setup(s=Depends(session),shop=Depends(owner)):
        st=core.settings(shop)
        return {'checks':[
            {'id':'email','name':'Email verified','ready':bool(s['verified']),'url':'/app/settings'},
            {'id':'prices','name':'Review your services and prices','ready':bool(st.get('prices_reviewed')),'url':'/app/services'},
            {'id':'branding','name':'Business branding','ready':bool(shop['name']),'url':'/app/branding'},
            {'id':'payments','name':'Payment method selected','ready':st['payment_method']=='in_person' or bool(shop['square_access'] and config.SQUARE_WEBHOOK),'url':'/app/settings'},
            {'id':'published','name':'Booking page published','ready':bool(shop['published']),'url':'/app/settings'}],
            'platform':{'email_configured':bool(config.MAIL_ENABLED and (config.RESEND_API_KEY or config.SMTP_HOST)),
                'email_enabled':config.MAIL_ENABLED,
                'email_mode':'disabled' if not config.MAIL_ENABLED else 'provider' if (config.RESEND_API_KEY or config.SMTP_HOST) else 'local-eml',
                'google_configured':bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET),
                'google_callback':config.BASE_URL+'/api/auth/google/callback',
                'billing_configured':plans.configured(),
                'square_configured':bool(config.SQUARE_ID and config.SQUARE_SECRET and config.SQUARE_WEBHOOK)},
            'note':'Configured means credentials are present, not that live delivery or payments have been verified.'}
