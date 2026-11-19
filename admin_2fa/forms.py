from django import forms
from django.utils.translation import gettext_lazy as _


class TokenForm(forms.Form):
    # Named `token` on purpose: Sentry's default scrubber blanks any request
    # field with that name, and the views mark it sensitive for Django's reports.
    token = forms.CharField(
        label=_("Code"),
        max_length=32,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "autofocus": True,
                "spellcheck": "false",
                "autocapitalize": "off",
            }
        ),
    )

    def clean_token(self):
        # Authenticator apps show "123 456"; people retype backup codes in caps.
        return "".join(self.cleaned_data["token"].split()).lower()
