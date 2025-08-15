# slayground/slayground/slayground_app/models.py
from __future__ import annotations

from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# -----------------------
# Shared / utilities
# -----------------------
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# -----------------------
# Core content
# -----------------------
class Instructor(TimeStampedModel):
    name = models.CharField(max_length=120)
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to="instructors/", blank=True, null=True)
    instagram_handle = models.CharField(max_length=64, blank=True)

    def __str__(self) -> str:
        return self.name


class Location(TimeStampedModel):
    name = models.CharField(max_length=120)
    address_line1 = models.CharField(max_length=200)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=80, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=80, default="USA")
    notes = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"{self.name} — {self.city}"


class ClassType(TimeStampedModel):
    """
    Reusable class definitions (e.g., Beginner Heels, Intermediate Grooves).
    """
    LEVEL_CHOICES = [
        ("BEGINNER", "Beginner"),
        ("INTERMEDIATE", "Intermediate"),
        ("MIXED", "Mixed Level"),
    ]

    title = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="MIXED")
    description = models.TextField(blank=True)
    thumbnail = models.ImageField(upload_to="class_types/", blank=True, null=True)
    default_duration_minutes = models.PositiveIntegerField(default=60)

    class Meta:
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title


class ClassSession(TimeStampedModel):
    """
    A scheduled instance of a ClassType that users can book.
    Treat this row as a "seed" when recurrence is enabled.
    """
    class_type = models.ForeignKey(ClassType, on_delete=models.PROTECT, related_name="sessions")
    instructor = models.ForeignKey(Instructor, on_delete=models.SET_NULL, null=True, related_name="sessions")
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions")

    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    capacity = models.PositiveIntegerField(default=20)
    price_cents = models.PositiveIntegerField(default=0)  # store money as integer (cents)

    is_published = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    # ---- Recurrence (kept inside ClassSession) ----
    # Enable this on a seed session, then run the admin action to generate copies.
    recurrence_enabled = models.BooleanField(default=False, help_text="If on, this session is a seed for repeats.")
    recurrence_every_n_weeks = models.PositiveIntegerField(default=1, help_text="1 = weekly, 2 = every other week, etc.")
    recurrence_until = models.DateField(null=True, blank=True, help_text="Last date to generate (inclusive).")
    # List of string dates "YYYY-MM-DD" to skip (holidays, closures)
    recurrence_skips = models.JSONField(default=list, blank=True, help_text='List of dates (YYYY-MM-DD) to skip')

    class Meta:
        ordering = ["start_datetime"]
        indexes = [
            models.Index(fields=["start_datetime"]),
            models.Index(fields=["class_type", "start_datetime"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["class_type", "start_datetime"],
                name="unique_classsession_type_start",
            )
        ]

    def __str__(self) -> str:
        return f"{self.class_type.title} @ {self.start_datetime:%Y-%m-%d %H:%M}"

    @property
    def spots_left(self) -> int:
        confirmed = self.bookings.filter(status="CONFIRMED").aggregate(
            s=models.Sum("quantity")
        )["s"] or 0
        return max(self.capacity - confirmed, 0)

    def clean(self):
        if self.end_datetime <= self.start_datetime:
            raise ValidationError("End time must be after start time.")

    def can_accept(self, qty: int) -> bool:
        return qty > 0 and self.spots_left >= qty

    # ---- recurrence generator based on this seed ----
    def _skip_set(self) -> set[date]:
        out: set[date] = set()
        for v in (self.recurrence_skips or []):
            try:
                if isinstance(v, str):
                    out.add(datetime.strptime(v, "%Y-%m-%d").date())
                elif isinstance(v, date):
                    out.add(v)
            except ValueError:
                # ignore malformed entries
                pass
        return out

    def generate_recurrences(self, *, default_weeks: int = 12, dry_run: bool = False) -> dict:
        """
        Using this row as a seed, generate future ClassSession rows at the same
        weekday/time every `recurrence_every_n_weeks`, up to `recurrence_until`
        (or default horizon if not set). Skips listed in `recurrence_skips`.
        """
        if not self.recurrence_enabled:
            return {"created": 0, "skipped": 0, "reason": "disabled"}

        end_date = self.recurrence_until or (self.start_datetime.date() + timedelta(weeks=default_weeks))
        if end_date < self.start_datetime.date():
            return {"created": 0, "skipped": 0, "reason": "empty-range"}

        duration = self.end_datetime - self.start_datetime
        skip_set = self._skip_set()

        created = 0
        skipped = 0

        # Use aware datetimes; adding weeks keeps TZ consistency naturally.
        cursor = self.start_datetime
        while True:
            cursor = cursor + timedelta(weeks=self.recurrence_every_n_weeks)
            if cursor.date() > end_date:
                break

            if cursor.date() in skip_set:
                skipped += 1
                continue

            start_dt = cursor
            end_dt = start_dt + duration

            exists = ClassSession.objects.filter(
                class_type=self.class_type,
                start_datetime=start_dt,
            ).exists()
            if exists:
                skipped += 1
                continue

            if not dry_run:
                ClassSession.objects.create(
                    class_type=self.class_type,
                    instructor=self.instructor,
                    location=self.location,
                    start_datetime=start_dt,
                    end_datetime=end_dt,
                    capacity=self.capacity,
                    price_cents=self.price_cents,
                    is_published=self.is_published,
                    notes=self.notes,
                )
            created += 1

        return {"created": created, "skipped": skipped, "reason": "ok"}



class Booking(TimeStampedModel):
    """
    A user's reservation for a ClassSession.
    """
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("CONFIRMED", "Confirmed"),
        ("CANCELLED", "Cancelled"),
        ("REFUNDED", "Refunded"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookings")
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="bookings")
    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    quantity = models.PositiveIntegerField(default=1)
    message = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="PENDING")
    paid_cents = models.PositiveIntegerField(default=0)
    # Stripe fields (future use)
    stripe_payment_intent = models.CharField(max_length=120, blank=True)
    stripe_receipt_url = models.URLField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gte=1), name="booking_quantity_gte_1"),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self) -> str:
        return f"Booking #{self.id} — {self.session} x{self.quantity} ({self.get_status_display()})"

    def clean(self):
        if self.status in {"PENDING", "CONFIRMED"} and not self.session.can_accept(self.quantity):
            raise ValidationError(f"Only {self.session.spots_left} spots left for this session.")

    @property
    def occurs_in_future(self) -> bool:
        return self.session.start_datetime >= timezone.now()


# -----------------------
# Online content
# -----------------------
class ContentCategory(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    requires_login = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class MediaItem(TimeStampedModel):
    VISIBILITY_CHOICES = [
        ("PUBLIC", "Public"),
        ("MEMBERS", "Members Only"),
    ]

    category = models.ForeignKey(ContentCategory, on_delete=models.CASCADE, related_name="items")
    title = models.CharField(max_length=160)
    summary = models.TextField(blank=True)

    # Media
    image = models.ImageField(upload_to="content/images/", blank=True, null=True)
    video_url = models.URLField(blank=True)
    audio_file = models.FileField(upload_to="content/audio/", blank=True, null=True)
    attachment = models.FileField(upload_to="content/attachments/", blank=True, null=True)
    external_url = models.URLField(blank=True, help_text="Optional generic link (blog, drive, etc.)")

    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default="PUBLIC")
    is_active = models.BooleanField(default=True)
    publish_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-publish_at", "-created_at"]

    def __str__(self) -> str:
        return self.title

    @property
    def is_live(self) -> bool:
        return self.is_active and (self.publish_at is None or self.publish_at <= timezone.now())

    def clean(self):
        if not any([self.image, self.video_url, self.audio_file, self.attachment, self.external_url]):
            raise ValidationError("Add at least one: image, video URL, audio file, attachment, or external URL.")

# -----------------------
# Events: SLAYvents & SLAYbrations
# -----------------------
class Event(TimeStampedModel):
    EVENT_TYPE_CHOICES = [
        ("PRIVATE", "SLAYvents (Private)"),
        ("PUBLIC", "SLAYbrations (Public)"),
    ]

    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=200, unique=True)
    event_type = models.CharField(max_length=10, choices=EVENT_TYPE_CHOICES)
    description = models.TextField(blank=True)

    start_datetime = models.DateTimeField(null=True, blank=True)
    end_datetime = models.DateTimeField(null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")

    capacity = models.PositiveIntegerField(default=0)  # 0 = unlimited (for PUBLIC)
    price_cents = models.PositiveIntegerField(default=0)  # 0 = free
    banner_image = models.ImageField(upload_to="events/", null=True, blank=True)

    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["-start_datetime", "title"]
        indexes = [
            models.Index(fields=["event_type", "is_published"]),
            models.Index(fields=["start_datetime"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_event_type_display()})"

    @property
    def is_public(self) -> bool:
        return self.event_type == "PUBLIC"

    @property
    def is_private(self) -> bool:
        return self.event_type == "PRIVATE"

    @property
    def spots_left(self) -> int | None:
        if self.capacity <= 0 or not self.is_public:
            return None
        confirmed = self.registrations.filter(status="CONFIRMED").aggregate(s=models.Sum("quantity"))["s"] or 0
        return max(self.capacity - confirmed, 0)

    def can_accept(self, qty: int) -> bool:
        if not self.is_public or self.capacity <= 0:
            return True
        return qty > 0 and (self.spots_left or 0) >= qty


class EventInquiry(TimeStampedModel):
    CATEGORY_CHOICES = [
        ("BACHELORETTE", "Bachelorette"),
        ("BIRTHDAY", "Birthday"),
        ("CORPORATE", "Corporate"),
        ("SCHOOL", "School/Team"),
        ("CUSTOM", "Custom"),
    ]
    STATUS_CHOICES = [
        ("NEW", "New"),
        ("CONTACTED", "Contacted"),
        ("BOOKED", "Booked"),
        ("CLOSED", "Closed"),
    ]

    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    preferred_date = models.DateField(null=True, blank=True)
    attendees_count = models.PositiveIntegerField(default=10)
    city_or_studio = models.CharField(max_length=160, blank=True)
    message = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="NEW")

    template_event = models.ForeignKey(
        Event, on_delete=models.SET_NULL, null=True, blank=True,
        limit_choices_to={"event_type": "PRIVATE"}, related_name="inquiries"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "category"])]

    def __str__(self):
        return f"{self.full_name} — {self.get_category_display()} ({self.status})"


class EventRegistration(TimeStampedModel):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("CONFIRMED", "Confirmed"),
        ("CANCELLED", "Cancelled"),
        ("REFUNDED", "Refunded"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_registrations")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="registrations")
    full_name = models.CharField(max_length=120)
    email = models.EmailField()
    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="PENDING")  # <-- fixed max_length
    paid_cents = models.PositiveIntegerField(default=0)

    stripe_payment_intent = models.CharField(max_length=120, blank=True)
    stripe_receipt_url = models.URLField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(check=models.Q(quantity__gte=1), name="event_reg_quantity_gte_1")
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["event", "status"]),
        ]

    def __str__(self):
        return f"Reg #{self.id} — {self.event.title} x{self.quantity} ({self.get_status_display()})"

    def clean(self):
        if not self.event.can_accept(self.quantity):
            raise ValidationError(f"Only {self.event.spots_left} spots left for this event.")