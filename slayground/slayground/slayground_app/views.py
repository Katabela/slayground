from __future__ import annotations

from datetime import datetime
from typing import List

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .forms import (
    BookingCreateForm,
    ClassSearchForm,
    QuickSessionCreateForm,
    SignUpForm,
)
from .models import (
    Booking,
    ClassSession,
    ClassType,
    ContentCategory,
    Instructor,
    Location,
    MediaItem,
)


# ---------------------------
# Public pages
# ---------------------------

@require_GET
def home(request: HttpRequest) -> HttpResponse:
    """Landing page: show a few upcoming classes and latest content."""
    upcoming = (
        ClassSession.objects.filter(is_published=True, start_datetime__gte=timezone.now())
        .select_related("class_type", "instructor", "location")
        .order_by("start_datetime")[:6]
    )

    latest_content = (
        MediaItem.objects.filter(is_active=True)
        .order_by("-publish_at", "-created_at")[:6]
    )
    # If the user is not authenticated, hide members-only items in the teaser row
    if not request.user.is_authenticated:
        latest_content = [m for m in latest_content if m.visibility == "PUBLIC"]

    return render(request, "sg/home.html", {
        "upcoming": upcoming,
        "latest_content": latest_content,
    })


@require_GET
def about(request: HttpRequest) -> HttpResponse:
    """Simple About page (brand values, testimonials, reels, etc.)."""
    return render(request, "sg/about.html")


@require_http_methods(["GET"])
def class_list(request: HttpRequest) -> HttpResponse:
    """
    Classes index with flexible filtering + pagination.
    Default view shows upcoming, published sessions.
    """
    qs = (
        ClassSession.objects.filter(is_published=True, start_datetime__gte=timezone.now())
        .select_related("class_type", "instructor", "location")
        .order_by("start_datetime")
    )

    form = ClassSearchForm(request.GET or None)
    if form.is_valid():
        qs = form.filter_queryset(qs)

    paginator = Paginator(qs, 10)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "sg/class_list.html", {
        "form": form,
        "page_obj": page_obj,
        "sessions": page_obj.object_list,
    })


@require_GET
def class_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Detail page for a single scheduled session."""
    session = get_object_or_404(
        ClassSession.objects.select_related("class_type", "instructor", "location"),
        pk=pk,
        is_published=True,
    )
    return render(request, "sg/class_detail.html", {"session": session})


@login_required
@require_http_methods(["GET", "POST"])
def book_session(request: HttpRequest, session_id: int) -> HttpResponse:
    """
    Booking form:
    - Validates capacity and time
    - Creates a PENDING booking (flip to CONFIRMED after Stripe/webhook)
    """
    session = get_object_or_404(
        ClassSession.objects.select_related("class_type", "instructor", "location"),
        pk=session_id,
        is_published=True,
    )

    form = BookingCreateForm(request.POST or None, session=session)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                booking: Booking = form.save(commit=False)
                booking.user = request.user
                booking.session = session
                # price is stored on session; booking holds paid_cents after payment
                booking.status = "PENDING"
                booking.save()
            messages.success(request, "Booking created! You can proceed to payment.")
            return redirect("sg:class_detail", pk=session.pk)
        except IntegrityError:
            messages.error(request, "We couldn’t create your booking. Please try again.")
        except Exception as e:
            messages.error(request, str(e))

    return render(request, "sg/book_session.html", {
        "form": form,
        "session": session,
    })


@require_http_methods(["GET"])
def content_hub(request: HttpRequest) -> HttpResponse:
    """
    Content landing page:
    - Shows categories
    - Lists items, hiding members-only items for anonymous users
    """
    categories = ContentCategory.objects.order_by("name")
    items_qs = MediaItem.objects.filter(is_active=True).order_by("-publish_at", "-created_at")

    if not request.user.is_authenticated:
        items_qs = items_qs.filter(visibility="PUBLIC")

    return render(request, "sg/content_hub.html", {
        "categories": categories,
        "items": items_qs[:24],  # simple cap for the grid; adjust as desired
    })


# ---------------------------
# Calendar
# ---------------------------

@require_GET
def calendar_view(request: HttpRequest) -> HttpResponse:
    """
    Renders the calendar page (FullCalendar front-end).
    FullCalendar will fetch events from `calendar_events`.
    """
    return render(request, "sg/calendar.html")


@require_GET
def calendar_events(request: HttpRequest) -> JsonResponse:
    """
    JSON feed for FullCalendar.

    Accepts optional GET params:
      - start (ISO8601)
      - end   (ISO8601)
    """
    start_param = request.GET.get("start")
    end_param = request.GET.get("end")

    events_qs = ClassSession.objects.filter(is_published=True).select_related("class_type")
    now = timezone.now()

    # Limit to future by default if no range provided
    if not start_param and not end_param:
        events_qs = events_qs.filter(start_datetime__gte=now)

    # Apply range filters if provided
    tz = timezone.get_current_timezone()
    try:
        if start_param:
            start_dt = datetime.fromisoformat(start_param)
            if timezone.is_naive(start_dt):
                start_dt = timezone.make_aware(start_dt, tz)
            events_qs = events_qs.filter(start_datetime__gte=start_dt)
        if end_param:
            end_dt = datetime.fromisoformat(end_param)
            if timezone.is_naive(end_dt):
                end_dt = timezone.make_aware(end_dt, tz)
            events_qs = events_qs.filter(start_datetime__lte=end_dt)
    except ValueError:
        # If parsing fails, ignore and fall back to default
        pass

    events: List[dict] = []
    for s in events_qs.order_by("start_datetime"):
        events.append({
            "id": s.id,
            "title": s.class_type.title,
            "start": s.start_datetime.isoformat(),
            "end": s.end_datetime.isoformat(),
            "url": reverse("sg:class_detail", kwargs={"pk": s.id}),
        })
    return JsonResponse(events, safe=False)


# ---------------------------
# Staff utilities (optional)
# ---------------------------

@staff_member_required
@require_http_methods(["GET", "POST"])
def quick_create_session(request: HttpRequest) -> HttpResponse:
    """
    Lightweight staff-only helper to create a ClassSession without visiting Django admin.
    """
    form = QuickSessionCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        ClassSession.objects.create(
            class_type=cd["class_type"],
            instructor=cd.get("instructor"),
            location=cd.get("location"),
            start_datetime=cd["start_datetime"],
            end_datetime=cd["end_datetime"],
            capacity=cd["capacity"],
            price_cents=cd["price_cents"],
            is_published=True,
        )
        messages.success(request, "Class session created.")
        return redirect("sg:class_list")

    return render(request, "sg/quick_create_session.html", {"form": form})

@require_http_methods(["GET", "POST"])
def signup(request):
    # If already logged in, bounce to where they were going (or home)
    next_url = request.GET.get("next") or request.POST.get("next") or reverse("sg:home")
    if request.user.is_authenticated:
        return redirect(next_url)

    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Welcome to Slayground! ✨ Your account is ready.")
        return redirect(next_url)

    return render(request, "registration/signup.html", {"form": form, "next": next_url})

from .forms import EventInquiryForm, EventRegistrationForm
from .models import Event

# -------- SLAYvents (private) --------
@require_http_methods(["GET", "POST"])
def slayvents(request: HttpRequest) -> HttpResponse:
    """
    Marketing + inquiry form for private events.
    """
    form = EventInquiryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        inquiry = form.save()
        messages.success(request, "Thank you! We’ll be in touch to plan your SLAYvent 🎉")
        return redirect("sg:slayvents")
    # Optional: showcase some private templates if you create them in admin
    templates = Event.objects.filter(event_type="PRIVATE", is_published=True).order_by("title")[:6]
    return render(request, "sg/slayvents.html", {"form": form, "templates": templates})

# -------- SLAYbrations (public) --------
@require_GET
def slaybrations_list(request: HttpRequest) -> HttpResponse:
    events = Event.objects.filter(event_type="PUBLIC", is_published=True).order_by("start_datetime")
    return render(request, "sg/slaybrations_list.html", {"events": events})

@require_GET
def slaybrations_detail(request: HttpRequest, slug: str) -> HttpResponse:
    event = get_object_or_404(Event, slug=slug, event_type="PUBLIC", is_published=True)
    return render(request, "sg/slaybrations_detail.html", {"event": event})

@login_required
@require_http_methods(["GET", "POST"])
def slaybrations_register(request: HttpRequest, slug: str) -> HttpResponse:
    event = get_object_or_404(Event, slug=slug, event_type="PUBLIC", is_published=True)
    form = EventRegistrationForm(request.POST or None, event=event)
    if request.method == "POST" and form.is_valid():
        reg = form.save(commit=False)
        reg.user = request.user
        reg.event = event
        reg.status = "PENDING"  # flip to CONFIRMED after payment, if charging
        reg.save()
        messages.success(request, "Registration received! We’ll email details shortly.")
        return redirect("sg:slaybrations_detail", slug=event.slug)
    return render(request, "sg/slaybrations_register.html", {"form": form, "event": event})
