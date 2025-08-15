# slayground/slayground/slayground_app/admin.py
import re
from datetime import datetime, timedelta

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ActionForm
from django.utils.html import format_html

from .models import (
    Instructor,
    Location,
    ClassType,
    ClassSession,
    Booking,
    ContentCategory,
    MediaItem,
    Event,
    EventInquiry,
    EventRegistration,
)

# ---------------------------
# Core admins
# ---------------------------

@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = ("name", "instagram_handle", "created_at")
    search_fields = ("name", "instagram_handle")


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "country")
    search_fields = ("name", "city", "state", "country")


@admin.register(ClassType)
class ClassTypeAdmin(admin.ModelAdmin):
    list_display = ("title", "level", "default_duration_minutes")
    prepopulated_fields = {"slug": ("title",)}
    search_fields = ("title",)
    list_filter = ("level",)


# ----- ClassSession admin + actions -----

class RepeatSessionsActionForm(ActionForm):
    occurrences = forms.IntegerField(
        min_value=1, initial=6,
        help_text="How many future sessions to create per selected session.",
    )
    every_n_weeks = forms.IntegerField(
        min_value=1, initial=1,
        help_text="1 = weekly, 2 = every other week, etc.",
    )
    skip_dates = forms.CharField(
        required=False,
        help_text="Dates to skip (YYYY-MM-DD), comma or newline separated.",
    )


@admin.register(ClassSession)
class ClassSessionAdmin(admin.ModelAdmin):
    list_display = (
        "class_type", "start_datetime", "end_datetime",
        "instructor", "capacity", "spots_left", "price_cents",
        "is_published", "recurrence_enabled",
    )
    list_filter = ("class_type", "instructor", "is_published", "start_datetime", "recurrence_enabled")
    autocomplete_fields = ("class_type", "instructor", "location")
    search_fields = ("class_type__title", "instructor__name", "location__name")

    fieldsets = (
        (None, {
            "fields": (
                "class_type", "instructor", "location",
                ("start_datetime", "end_datetime"),
                ("capacity", "price_cents"), "is_published", "notes",
            )
        }),
        ("Recurrence (optional)", {
            "classes": ("collapse",),
            "fields": (
                "recurrence_enabled",
                "recurrence_every_n_weeks",
                "recurrence_until",
                "recurrence_skips",
            )
        }),
    )

    action_form = RepeatSessionsActionForm
    actions = ["generate_repeats", "generate_from_recurrence_fields"]

    @admin.action(description="Generate repeats (weekly) from selected (manual options)")
    def generate_repeats(self, request, queryset):
        form = RepeatSessionsActionForm(request.POST)
        if not form.is_valid():
            self.message_user(request, "Please provide valid repeat options.", level=messages.ERROR)
            return

        occurrences = form.cleaned_data["occurrences"]
        interval = form.cleaned_data["every_n_weeks"]
        raw_skips = (form.cleaned_data.get("skip_dates") or "").strip()

        skip_set = set()
        if raw_skips:
            for token in re.split(r"[,\s]+", raw_skips):
                if not token:
                    continue
                try:
                    skip_set.add(datetime.strptime(token, "%Y-%m-%d").date())
                except ValueError:
                    pass

        created = 0
        skipped = 0

        for seed in queryset:
            duration = seed.end_datetime - seed.start_datetime
            for i in range(1, occurrences + 1):
                start_dt = seed.start_datetime + timedelta(weeks=interval * i)
                if start_dt.date() in skip_set:
                    skipped += 1
                    continue
                end_dt = start_dt + duration

                exists = ClassSession.objects.filter(
                    class_type=seed.class_type,
                    start_datetime=start_dt,
                ).exists()
                if exists:
                    skipped += 1
                    continue

                ClassSession.objects.create(
                    class_type=seed.class_type,
                    instructor=seed.instructor,
                    location=seed.location,
                    start_datetime=start_dt,
                    end_datetime=end_dt,
                    capacity=seed.capacity,
                    price_cents=seed.price_cents,
                    is_published=seed.is_published,
                    notes=seed.notes,
                )
                created += 1

        self.message_user(request, f"Created {created} session(s); skipped {skipped}.", level=messages.INFO)

    @admin.action(description="Generate from each session’s recurrence fields")
    def generate_from_recurrence_fields(self, request, queryset):
        total_created = 0
        total_skipped = 0
        for seed in queryset:
            res = seed.generate_recurrences(default_weeks=12, dry_run=False)
            total_created += res.get("created", 0)
            total_skipped += res.get("skipped", 0)
        self.message_user(
            request,
            f"Generated {total_created} session(s); skipped {total_skipped}.",
            level=messages.INFO,
        )


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "session", "status", "quantity", "paid_cents", "created_at")
    list_filter = ("status", "session__class_type")
    search_fields = ("full_name", "email", "stripe_payment_intent")
    autocomplete_fields = ("user", "session")


@admin.register(ContentCategory)
class ContentCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "requires_login", "created_at")
    search_fields = ("name", "slug")        # ← add this line
    prepopulated_fields = {"slug": ("name",)}


@admin.register(MediaItem)
class MediaItemAdmin(admin.ModelAdmin):
    list_display = ("thumb", "title", "category", "visibility", "is_active", "publish_at")
    list_filter = ("category", "visibility", "is_active")
    search_fields = ("title", "summary")
    autocomplete_fields = ("category",)
    readonly_fields = ("thumb_large",)
    fieldsets = (
        (None, {"fields": ("title", "category", "summary", "visibility", "is_active", "publish_at")}),
        ("Media", {
            "fields": ("image", "video_url", "audio_file", "attachment", "external_url", "thumb_large"),
            "description": "Provide at least one media/link. Image is used as the card header if present."
        }),
    )

    def thumb(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="height:40px;width:60px;object-fit:cover;border-radius:6px;border:1px solid #444;" />', obj.image.url)
        return "—"
    thumb.short_description = " "

    def thumb_large(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-width:320px;border-radius:8px;border:1px solid #444;" />', obj.image.url)
        return "—"
    thumb_large.short_description = "Preview"


# ---------------------------
# Events
# ---------------------------

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "event_type", "start_datetime", "location", "capacity", "price_cents", "is_published")
    list_filter = ("event_type", "is_published", "start_datetime", "location")
    search_fields = ("title", "description", "location__name", "location__city")
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ("location",)


@admin.register(EventInquiry)
class EventInquiryAdmin(admin.ModelAdmin):
    list_display = ("full_name", "category", "preferred_date", "attendees_count", "status", "created_at")
    list_filter = ("category", "status", "preferred_date")
    search_fields = ("full_name", "email", "phone", "message")
    autocomplete_fields = ("template_event",)


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "event", "status", "quantity", "paid_cents", "created_at")
    list_filter = ("status", "event__event_type")
    search_fields = ("full_name", "email", "stripe_payment_intent")
    autocomplete_fields = ("user", "event")
