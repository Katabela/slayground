from __future__ import annotations
from datetime import datetime
from django import forms
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import Booking, ClassSession, ClassType, Instructor, Location, EventInquiry, EventRegistration, Event


class BookingCreateForm(forms.ModelForm):
    """
    Public booking form.
    - You will set `session` and `user` in the view, not here.
    - Validates capacity and disallows past sessions.
    """
    quantity = forms.IntegerField(min_value=1, max_value=20, initial=1, help_text="How many spots?")

    class Meta:
        model = Booking
        fields = ["full_name", "email", "quantity", "message"]
        widgets = {
            "full_name": forms.TextInput(attrs={"placeholder": "Your name"}),
            "email": forms.EmailInput(attrs={"placeholder": "you@example.com"}),
            "message": forms.Textarea(attrs={"rows": 3, "placeholder": "Any notes for the instructor?"}),
        }

    def __init__(self, *args, session: ClassSession | None = None, **kwargs):
        """
        Pass the session instance from your view:
        form = BookingCreateForm(request.POST or None, session=session)
        """
        self.session = session
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()

        if not self.session:
            raise forms.ValidationError("No class session selected.")

        # Don’t allow booking past sessions
        if self.session.start_datetime < timezone.now():
            raise forms.ValidationError("This class has already started or finished.")

        qty = cleaned.get("quantity") or 1

        # Capacity check
        if not self.session.can_accept(qty):
            self.add_error("quantity", f"Only {self.session.spots_left} spots left.")

        return cleaned

class ClassSearchForm(forms.Form):
    """
    Filters for the Classes page.
    All fields optional; combine as a flexible filter.
    """
    class_type = forms.ModelChoiceField(
        queryset=ClassType.objects.all().order_by("title"),
        required=False,
        empty_label="Any type",
    )
    level = forms.ChoiceField(
        choices=[("", "Any level")] + list(ClassType.LEVEL_CHOICES),
        required=False,
    )
    instructor = forms.ModelChoiceField(
        queryset=Instructor.objects.all().order_by("name"),
        required=False,
        empty_label="Any instructor",
    )
    location = forms.ModelChoiceField(
        queryset=Location.objects.all().order_by("city", "name"),
        required=False,
        empty_label="Any location",
    )
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def filter_queryset(self, qs):
        """Apply filters to a ClassSession queryset."""
        cd = self.cleaned_data
        if cd.get("class_type"):
            qs = qs.filter(class_type=cd["class_type"])
        if cd.get("level"):
            qs = qs.filter(class_type__level=cd["level"])
        if cd.get("instructor"):
            qs = qs.filter(instructor=cd["instructor"])
        if cd.get("location"):
            qs = qs.filter(location=cd["location"])
        if cd.get("date_from"):
            start = datetime.combine(cd["date_from"], datetime.min.time()).astimezone(timezone.get_current_timezone())
            qs = qs.filter(start_datetime__gte=start)
        if cd.get("date_to"):
            end = datetime.combine(cd["date_to"], datetime.max.time()).astimezone(timezone.get_current_timezone())
            qs = qs.filter(start_datetime__lte=end)
        return qs

class QuickSessionCreateForm(forms.Form):
    """
    (Optional) A tiny helper for staff to quickly schedule a session from the site (not admin).
    Useful if you add a protected staff-only view later.
    """
    class_type = forms.ModelChoiceField(queryset=ClassType.objects.all())
    instructor = forms.ModelChoiceField(queryset=Instructor.objects.all(), required=False)
    location = forms.ModelChoiceField(queryset=Location.objects.all(), required=False)
    start_datetime = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    end_datetime = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    capacity = forms.IntegerField(min_value=1, initial=20)
    price_cents = forms.IntegerField(min_value=0, initial=0)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_datetime")
        end = cleaned.get("end_datetime")
        if start and end and end <= start:
            raise forms.ValidationError("End time must be after start time.")
        return cleaned

class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=30, required=False)
    last_name = forms.CharField(max_length=150, required=False)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        User = get_user_model()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email
    
class EventInquiryForm(forms.ModelForm):
    class Meta:
        model = EventInquiry
        fields = [
            "full_name", "email", "phone", "category",
            "preferred_date", "attendees_count", "city_or_studio", "message",
        ]
        widgets = {
            "preferred_date": forms.DateInput(attrs={"type": "date"}),
            "message": forms.Textarea(attrs={"rows": 4, "placeholder": "Tell us your vibe, music style, heels/no heels…"}),
        }

class EventRegistrationForm(forms.ModelForm):
    quantity = forms.IntegerField(min_value=1, max_value=20, initial=1)

    class Meta:
        model = EventRegistration
        fields = ["full_name", "email", "quantity"]

    def __init__(self, *args, event: Event | None = None, **kwargs):
        self.event = event
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if not self.event or not self.event.is_public:
            raise forms.ValidationError("Invalid event.")
        if self.event.start_datetime and self.event.start_datetime < timezone.now():
            raise forms.ValidationError("This event has already started or finished.")
        qty = cleaned.get("quantity") or 1
        if not self.event.can_accept(qty):
            self.add_error("quantity", f"Only {self.event.spots_left} spots left.")
        return cleaned
    