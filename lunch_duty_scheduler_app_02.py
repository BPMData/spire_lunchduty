import streamlit as st
import pandas as pd
import numpy as np
import random
from datetime import datetime, date, timedelta
import io
from io import BytesIO
import zipfile

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from PIL import Image, ImageDraw, ImageFont

st.set_page_config(page_title="Lunch Duty Scheduler v2", page_icon="🍽️", layout="wide")


# ==================== CONSTANTS ====================
class ScheduleConfig:
    """Configuration constants for schedule generation and display"""
    # PNG Image dimensions
    CELL_WIDTH = 250
    CELL_HEIGHT = 60
    HEADER_HEIGHT = 60
    TITLE_HEIGHT = 80
    PADDING = 20
    WEEK_SPACING = 30

    # Colors
    MAROON = '#8B0000'
    PINK = '#FFB6D9'
    LIGHT_GRAY = '#F5F5F5'
    NO_LUNCH_GRAY = '#E0E0E0'
    BORDER = '#CCCCCC'

    # PDF dimensions
    PDF_COL_WIDTH = 2.5 * inch
    PDF_ROW_HEIGHT = 0.5 * inch


st.title("🍽️ Spire School Lunch Duty Scheduler V2.0")
st.markdown("*Gap‑maximizing fair scheduling for Mon/Tue/Wed lunch duties*")

# ==================== SESSION STATE INITIALIZATION ====================
if "schedule_df" not in st.session_state:
    st.session_state.schedule_df = None
if "period_calendar_df" not in st.session_state:
    st.session_state.period_calendar_df = None
if "summary_df" not in st.session_state:
    st.session_state.summary_df = None
if "schedule_ready" not in st.session_state:
    st.session_state.schedule_ready = False
if "period_name" not in st.session_state:
    st.session_state.period_name = "Full Year"
if "month_name" not in st.session_state:
    st.session_state.month_name = "Full Year"
if "year_val" not in st.session_state:
    st.session_state.year_val = 2025
if "quota_warnings" not in st.session_state:
    st.session_state.quota_warnings = []


# ==================== HELPER FUNCTIONS ====================

def merge_calendar_with_schedule(period_calendar_df, schedule_df):
    """Merge all calendar days with duty assignments"""
    period_calendar_df['date_parsed'] = pd.to_datetime(period_calendar_df['date_parsed'])
    schedule_df['date_parsed'] = pd.to_datetime(schedule_df['date_parsed'])

    return period_calendar_df.merge(
        schedule_df.drop(columns=['date'], errors='ignore'),
        on=['date_parsed', 'day_of_week'],
        how='left'
    )


def load_fonts(sizes={'title': 32, 'header': 16, 'cell': 14}):
    """Load fonts with fallback options"""
    font_paths = [
        ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    ]

    fonts = {}
    for font_type, size in sizes.items():
        loaded = False
        for bold_path, regular_path in font_paths:
            try:
                path = regular_path if 'cell' in font_type else bold_path
                fonts[font_type] = ImageFont.truetype(path, size)
                loaded = True
                break
            except IOError:
                continue
        if not loaded:
            fonts[font_type] = ImageFont.load_default()

    return fonts


def check_schedule_conflicts(schedule_df, staff_df):
    """Check for scheduling conflicts and warnings"""
    if schedule_df is None or schedule_df.empty:
        return []

    issues = []

    # Check for UNASSIGNED slots
    unassigned_counts = {
        'main_room_1': (schedule_df['main_room_1'] == 'UNASSIGNED').sum(),
        'main_room_2': (schedule_df['main_room_2'] == 'UNASSIGNED').sum(),
        'quiet_room': (schedule_df['quiet_room'] == 'UNASSIGNED').sum()
    }

    total_unassigned = sum(unassigned_counts.values())
    if total_unassigned > 0:
        issues.append(f"⚠️ {total_unassigned} slots could not be assigned")
        for room, count in unassigned_counts.items():
            if count > 0:
                issues.append(f"  - {room}: {count} unassigned")

    # Check staff workload distribution
    all_assignments = pd.concat([
        schedule_df['main_room_1'],
        schedule_df['main_room_2'],
        schedule_df['quiet_room']
    ])
    assignment_counts = all_assignments[all_assignments != 'UNASSIGNED'].value_counts()

    if len(assignment_counts) > 0:
        max_duties = assignment_counts.max()
        min_duties = assignment_counts.min()
        if max_duties - min_duties > 2:
            issues.append(f"⚠️ Large workload imbalance: {min_duties}-{max_duties} duties per person")

    # Check for days with multiple staff who have the anti-pairing tag
    anti_col = 'should_not_be_paired_with_others_with_this_tag'
    if anti_col in staff_df.columns:
        staff_with_anti_tag = set(staff_df[staff_df[anti_col] == 1]['name'].tolist())
    else:
        staff_with_anti_tag = set()

    if len(staff_with_anti_tag) > 0:
        days_with_multiple_tagged = []
        for idx, row in schedule_df.iterrows():
            staff_on_duty = [row['main_room_1'], row['main_room_2'], row['quiet_room']]
            tagged_on_duty = [s for s in staff_on_duty if s in staff_with_anti_tag]

            if len(tagged_on_duty) > 1:
                days_with_multiple_tagged.append({
                    'date': row['date'],
                    'staff': tagged_on_duty
                })

        if days_with_multiple_tagged:
            issues.append(
                f"⚠️ {len(days_with_multiple_tagged)} day(s) have multiple staff with the 'avoid pairing' tag:")
            for day_info in days_with_multiple_tagged[:5]:
                issues.append(f"  - {day_info['date']}: {', '.join(day_info['staff'])}")
            if len(days_with_multiple_tagged) > 5:
                issues.append(f"  - ...and {len(days_with_multiple_tagged) - 5} more")

    return issues


# ==================== PHASE 1: EXACT QUOTA CALCULATION ====================

def calculate_exact_quotas(duty_days_df, staff_df, seed=None):
    """
    Calculate exact minimum duties per person, respecting availability.
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    staff_names = staff_df['name'].tolist()
    total_slots = len(duty_days_df) * 3
    base = total_slots // len(staff_names)
    remainder = total_slots % len(staff_names)

    # Initial equal assignment
    quotas = {name: base for name in staff_names}
    extra_staff = random.sample(staff_names, remainder)
    for name in extra_staff:
        quotas[name] += 1

    # --- Availability sanity check ---
    weeks_in_period = duty_days_df['date_parsed'].dt.isocalendar().apply(
        lambda x: f"{x.year}-{x.week}", axis=1
    ).nunique()

    # Build a fast lookup: (staff_name, day_of_week) -> is available
    staff_avail = {}
    for _, row in staff_df.iterrows():
        name = row['name']
        staff_avail[name] = {'Monday': row['Monday'], 'Tuesday': row['Tuesday'], 'Wednesday': row['Wednesday']}

    # Count available duty days in the period for each staff member
    available_counts = {name: 0 for name in staff_names}
    for _, day_row in duty_days_df.iterrows():
        dow = day_row['day_of_week']
        for name in staff_names:
            if staff_avail[name].get(dow, 0) == 1:
                available_counts[name] += 1

    warnings = []
    excess_slots = 0

    # Clamp any over‑ambitious quotas
    for name in staff_names:
        max_feasible = min(weeks_in_period, available_counts[name])
        if quotas[name] > max_feasible:
            excess = quotas[name] - max_feasible
            warnings.append(
                f"Staff '{name}' quota reduced from {quotas[name]} to {max_feasible} "
                f"(available only {available_counts[name]} days in {weeks_in_period} weeks)"
            )
            excess_slots += excess
            quotas[name] = max_feasible

    # Redistribute excess slots to people who still have capacity
    if excess_slots > 0:
        candidates = []
        for name in staff_names:
            max_feasible = min(weeks_in_period, available_counts[name])
            if quotas[name] < max_feasible:
                candidates.append(name)

        if candidates:
            for _ in range(excess_slots):
                if not candidates:
                    break
                pick = random.choice(candidates)
                quotas[pick] += 1
                max_feasible = min(weeks_in_period, available_counts[pick])
                if quotas[pick] >= max_feasible:
                    candidates.remove(pick)

        remaining_excess = sum(
            max(0, quotas[n] - min(weeks_in_period, available_counts[n]))
            for n in staff_names
        )
        if remaining_excess > 0:
            warnings.append(
                f"⚠️ {remaining_excess * 3} slot(s) may remain unassigned: "
                f"not enough staff availability for {total_slots} required slots."
            )

    warnings.sort()
    return quotas, warnings


# ==================== PHASE 2: GAP-MAXIMIZING SCHEDULE ====================

def generate_gap_maximizing_schedule(duty_days_df, staff_df, seed=None):
    """
    Generate a lunch duty schedule that:
    - Gives every person their exact minimum quota
    - Maximizes calendar‑day gaps between each person's duties
    - Randomizes day‑of‑week patterns so nobody always gets the same weekday
    - Only then attempts minor accommodations for pairing tags
    """
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    staff_names = staff_df['name'].tolist()

    # Phase 1: exact quotas
    quotas, quota_warnings = calculate_exact_quotas(duty_days_df, staff_df, seed=seed)

    # Tracking variables
    duty_count = {name: 0 for name in staff_names}
    quiet_room_count = {name: 0 for name in staff_names}
    last_duty_date = {name: None for name in staff_names}
    dow_counts = {name: {'Monday': 0, 'Tuesday': 0, 'Wednesday': 0} for name in staff_names}
    weekly_assignments = {}

    date_to_dow = {}
    for _, row in duty_days_df.iterrows():
        date_to_dow[row['date_parsed'].date()] = row['day_of_week']

    all_duty_dates = sorted([row['date_parsed'].date() for _, row in duty_days_df.iterrows()])
    total_span_days = (all_duty_dates[-1] - all_duty_dates[0]).days if len(all_duty_dates) > 1 else 1

    schedule = []

    for idx, row in duty_days_df.iterrows():
        date_str = row['date']
        date_parsed = row['date_parsed']
        day_of_week = row['day_of_week']
        date_obj = date_parsed.date()
        iso_year, iso_week, _ = date_parsed.isocalendar()
        week_key = (iso_year, iso_week)

        # ── Step 1: Build eligible pool ──
        eligible = []
        for name in staff_names:
            if staff_df[staff_df['name'] == name][day_of_week].values[0] != 1:
                continue
            if duty_count[name] >= quotas[name]:
                continue
            if week_key in weekly_assignments and name in weekly_assignments[week_key]:
                continue
            eligible.append(name)

        # ── Step 2: Relax weekly constraint if needed ──
        if len(eligible) < 3:
            eligible = []
            for name in staff_names:
                if staff_df[staff_df['name'] == name][day_of_week].values[0] != 1:
                    continue
                if duty_count[name] >= quotas[name]:
                    continue
                eligible.append(name)

        # ── Step 3: Panic mode (relax quota) ──
        if len(eligible) < 3:
            eligible = []
            for name in staff_names:
                if staff_df[staff_df['name'] == name][day_of_week].values[0] != 1:
                    continue
                eligible.append(name)

        # ── Step 4: Score candidates ──
        ideal_gap = max(1, total_span_days / max(1, sum(quotas.values()) / len(staff_names)))

        def score_candidate(name):
            remaining = quotas[name] - duty_count[name]
            urgency = remaining / max(1, quotas[name])

            if last_duty_date[name] is None:
                gap_score = 1.0
            else:
                days_since = (date_obj - last_duty_date[name]).days
                gap_score = min(1.0, days_since / ideal_gap)

            total_assigned = duty_count[name]
            if total_assigned > 0:
                dow_balance = 1.0 - (dow_counts[name][day_of_week] / total_assigned)
            else:
                dow_balance = 1.0

            random_jitter = random.uniform(0, 0.15)

            return urgency * 5.0 + gap_score * 0.55 + dow_balance * 0.30 + random_jitter * 0.15

        scored = [(name, score_candidate(name)) for name in eligible]

        # Sort solely by the score (which includes the urgency scalar)
        scored.sort(key=lambda x: x[1], reverse=True)

        # ── Step 5: Select top 3 ──
        selected = [s[0] for s in scored[:3]]

        while len(selected) < 3:
            for name in eligible:
                if name not in selected:
                    selected.append(name)
                    if len(selected) >= 3:
                        break

        while len(selected) < 3:
            selected.append('UNASSIGNED')

        # ── Step 6: Assign rooms (balance quiet room) ──
        selected_sorted = sorted(
            [s for s in selected if s != 'UNASSIGNED'],
            key=lambda x: quiet_room_count.get(x, 0)
        )
        unassigned_count = sum(1 for s in selected if s == 'UNASSIGNED')

        if len(selected_sorted) >= 1:
            if len(selected_sorted) >= 3:
                quiet_person = selected_sorted[0]
                main_candidates = selected_sorted[1:]
                random.shuffle(main_candidates)
                quiet_room = quiet_person
                main_1 = main_candidates[0]
                main_2 = main_candidates[1] if len(main_candidates) > 1 else 'UNASSIGNED'
            elif len(selected_sorted) == 2:
                quiet_room = selected_sorted[0]
                main_1 = selected_sorted[1]
                main_2 = 'UNASSIGNED'
            else:
                quiet_room = selected_sorted[0]
                main_1 = 'UNASSIGNED'
                main_2 = 'UNASSIGNED'
        else:
            quiet_room = 'UNASSIGNED'
            main_1 = 'UNASSIGNED'
            main_2 = 'UNASSIGNED'

        # ── Step 7: Update tracking ──
        for staff in [quiet_room, main_1, main_2]:
            if staff != 'UNASSIGNED':
                duty_count[staff] += 1
                last_duty_date[staff] = date_obj
                dow_counts[staff][day_of_week] += 1
                if week_key not in weekly_assignments:
                    weekly_assignments[week_key] = set()
                weekly_assignments[week_key].add(staff)

        if quiet_room != 'UNASSIGNED':
            quiet_room_count[quiet_room] += 1

        schedule.append({
            'date': date_str,
            'date_parsed': date_parsed,
            'day_of_week': day_of_week,
            'main_room_1': main_1,
            'main_room_2': main_2,
            'quiet_room': quiet_room
        })

    schedule_df = pd.DataFrame(schedule)

    summary = pd.DataFrame({
        'staff_name': staff_names,
        'target_quota': [quotas.get(name, 0) for name in staff_names],
        'actual_duties': [duty_count[name] for name in staff_names],
        'quiet_room_duties': [quiet_room_count[name] for name in staff_names],
        'main_room_duties': [duty_count[name] - quiet_room_count[name] for name in staff_names]
    })
    summary = summary.sort_values('actual_duties', ascending=False)

    return schedule_df, summary, quotas, quota_warnings


# ==================== PHASE 3: TAG POST-PROCESSING SWAPS ====================

def apply_tag_swaps(schedule_df, staff_df, quotas, seed=None):
    """
    Post-processing: attempt minor swaps to accommodate pairing tags.
    Only makes changes that don't violate quotas, weekly constraints, or availability.
    This runs AFTER the fair schedule is built.
    """
    if seed is not None:
        random.seed(seed + 9999)
        np.random.seed(seed + 9999)

    schedule = schedule_df.copy()
    schedule['date_parsed'] = pd.to_datetime(schedule['date_parsed'])

    pro_col = 'should_TRY_TO_pair_with_others_with_this_tag'
    anti_col = 'should_not_be_paired_with_others_with_this_tag'

    pro_tagged = set()
    anti_tagged = set()

    if pro_col in staff_df.columns:
        pro_tagged = set(staff_df[staff_df[pro_col] == 1]['name'].tolist())
    if anti_col in staff_df.columns:
        anti_tagged = set(staff_df[staff_df[anti_col] == 1]['name'].tolist())

    if len(pro_tagged) == 0 and len(anti_tagged) == 0:
        return schedule

    date_assignments = {}
    for idx, row in schedule.iterrows():
        d = row['date_parsed'].date()
        date_assignments[d] = {
            'main_room_1': row['main_room_1'],
            'main_room_2': row['main_room_2'],
            'quiet_room': row['quiet_room'],
            'day_of_week': row['day_of_week'],
            'idx': idx
        }

    staff_avail = {}
    for _, srow in staff_df.iterrows():
        name = srow['name']
        staff_avail[name] = {
            'Monday': srow.get('Monday', 0),
            'Tuesday': srow.get('Tuesday', 0),
            'Wednesday': srow.get('Wednesday', 0)
        }

    def swap_is_valid(staff_a, staff_b, date_a, date_b):
        """Check if swapping staff_a (on date_a) with staff_b (on date_b) is valid."""
        if staff_a == 'UNASSIGNED' or staff_b == 'UNASSIGNED':
            return False
        if staff_a == staff_b:
            return False

        # Same‑day collision check
        slots_a = [date_assignments[date_a]['main_room_1'],
                   date_assignments[date_a]['main_room_2'],
                   date_assignments[date_a]['quiet_room']]
        slots_b = [date_assignments[date_b]['main_room_1'],
                   date_assignments[date_b]['main_room_2'],
                   date_assignments[date_b]['quiet_room']]

        if staff_a in slots_b:
            return False
        if staff_b in slots_a:
            return False

        dow_a = date_assignments[date_a]['day_of_week']
        dow_b = date_assignments[date_b]['day_of_week']

        if staff_avail.get(staff_a, {}).get(dow_b, 0) != 1:
            return False
        if staff_avail.get(staff_b, {}).get(dow_a, 0) != 1:
            return False

        iso_a = date_a.isocalendar()[:2]
        iso_b = date_b.isocalendar()[:2]

        for d, assign in date_assignments.items():
            if d == date_a or d == date_b:
                continue
            if d.isocalendar()[:2] == iso_b:
                slots = [assign['main_room_1'], assign['main_room_2'], assign['quiet_room']]
                if staff_a in slots:
                    return False
            if d.isocalendar()[:2] == iso_a:
                slots = [assign['main_room_1'], assign['main_room_2'], assign['quiet_room']]
                if staff_b in slots:
                    return False
        return True

    # ── PRO-PAIRING: Try to get at least 2 pro-tagged staff together ──
    if len(pro_tagged) >= 2:
        for d, assign in date_assignments.items():
            slots = [assign['main_room_1'], assign['main_room_2'], assign['quiet_room']]
            pro_on_day = [s for s in slots if s in pro_tagged]

            if len(pro_on_day) == 1:
                for other_d, other_assign in date_assignments.items():
                    if other_d == d:
                        continue
                    if abs((d - other_d).days) > 14:
                        continue

                    other_slots = [other_assign['main_room_1'], other_assign['main_room_2'], other_assign['quiet_room']]
                    other_pro = [s for s in other_slots if s in pro_tagged]

                    if len(other_pro) >= 1:
                        pro_to_bring = other_pro[0]
                        non_pro_on_our_day = [s for s in slots if s not in pro_tagged and s != 'UNASSIGNED']

                        if non_pro_on_our_day and swap_is_valid(non_pro_on_our_day[0], pro_to_bring, d, other_d):
                            our_slot_type = None
                            for st in ['main_room_1', 'main_room_2', 'quiet_room']:
                                if assign[st] == non_pro_on_our_day[0]:
                                    our_slot_type = st
                                    break
                            their_slot_type = None
                            for st in ['main_room_1', 'main_room_2', 'quiet_room']:
                                if other_assign[st] == pro_to_bring:
                                    their_slot_type = st
                                    break

                            if our_slot_type and their_slot_type:
                                schedule.at[assign['idx'], our_slot_type] = pro_to_bring
                                schedule.at[other_assign['idx'], their_slot_type] = non_pro_on_our_day[0]
                                date_assignments[d][our_slot_type] = pro_to_bring
                                date_assignments[other_d][their_slot_type] = non_pro_on_our_day[0]
                                break  # Prevent infinite swaps for this day

    # ── ANTI-PAIRING: Try to separate anti-tagged staff on same day ──
    if len(anti_tagged) > 0:
        for d, assign in list(date_assignments.items()):
            slots = [assign['main_room_1'], assign['main_room_2'], assign['quiet_room']]
            anti_on_day = [s for s in slots if s in anti_tagged]

            if len(anti_on_day) >= 2:
                for anti_person in anti_on_day:
                    slot_type = None
                    for st in ['main_room_1', 'main_room_2', 'quiet_room']:
                        if assign[st] == anti_person:
                            slot_type = st
                            break

                    for other_d, other_assign in date_assignments.items():
                        if other_d == d:
                            continue
                        if abs((d - other_d).days) > 14:
                            continue

                        other_slots = [other_assign['main_room_1'], other_assign['main_room_2'],
                                       other_assign['quiet_room']]
                        non_anti_on_other = [s for s in other_slots if s not in anti_tagged and s != 'UNASSIGNED']

                        for swap_candidate in non_anti_on_other:
                            if swap_is_valid(anti_person, swap_candidate, d, other_d):
                                their_slot_type = None
                                for st in ['main_room_1', 'main_room_2', 'quiet_room']:
                                    if other_assign[st] == swap_candidate:
                                        their_slot_type = st
                                        break

                                if slot_type and their_slot_type:
                                    schedule.at[assign['idx'], slot_type] = swap_candidate
                                    schedule.at[other_assign['idx'], their_slot_type] = anti_person
                                    date_assignments[d][slot_type] = swap_candidate
                                    date_assignments[other_d][their_slot_type] = anti_person
                                    break

                        if len([s for s in [schedule.at[assign['idx'], 'main_room_1'],
                                            schedule.at[assign['idx'], 'main_room_2'],
                                            schedule.at[assign['idx'], 'quiet_room']] if s in anti_tagged]) < 2:
                            break

                    if len([s for s in [schedule.at[assign['idx'], 'main_room_1'],
                                        schedule.at[assign['idx'], 'main_room_2'],
                                        schedule.at[assign['idx'], 'quiet_room']] if s in anti_tagged]) < 2:
                        break

    return schedule


# ==================== PDF EXPORT ====================

def create_pdf_schedule(schedule_df, period_calendar_df, month_name, year):
    """Create a nicely formatted PDF of the schedule"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), topMargin=0.3 * inch, bottomMargin=0.3 * inch)
    elements = []
    styles = getSampleStyleSheet()

    main_title_style = ParagraphStyle(
        'MainTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=colors.HexColor(ScheduleConfig.MAROON),
        spaceAfter=0.5 * inch,
        alignment=1
    )
    month_title_style = ParagraphStyle(
        'MonthTitle',
        parent=styles['Heading2'],
        fontSize=20,
        textColor=colors.HexColor(ScheduleConfig.MAROON),
        spaceAfter=0.3 * inch,
        alignment=0
    )
    legend_style = ParagraphStyle(
        'Legend',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.grey,
        alignment=0
    )

    merged_df = merge_calendar_with_schedule(period_calendar_df, schedule_df)
    merged_df['week'] = merged_df['date_parsed'].dt.isocalendar().week
    merged_df['month'] = merged_df['date_parsed'].dt.month
    merged_df['year'] = merged_df['date_parsed'].dt.year

    if month_name == "Full Year":
        min_yr = merged_df['year'].min()
        max_yr = merged_df['year'].max()
        year_range = str(min_yr) if min_yr == max_yr else f"{min_yr}-{max_yr}"

        title = Paragraph("Spire School Lunch Duty Schedule", main_title_style)
        subtitle = Paragraph(f"Full {year_range} Academic Year", month_title_style)
        elements.append(title)
        elements.append(subtitle)
        elements.append(PageBreak())

        unique_months = merged_df[['year', 'month']].drop_duplicates().sort_values(['year', 'month'])

        for _, row in unique_months.iterrows():
            current_year = row['year']
            current_month = row['month']

            month_df = merged_df[(merged_df['year'] == current_year) &
                                 (merged_df['month'] == current_month)]

            if len(month_df) == 0:
                continue

            month_name_str = month_df['date_parsed'].iloc[0].strftime('%B')
            month_title = Paragraph(f"{month_name_str} {current_year} - Lunch Duty Schedule", month_title_style)
            elements.append(month_title)

            weeks_in_month = month_df['week'].unique()
            for week_num in sorted(weeks_in_month):
                week_data = month_df[month_df['week'] == week_num]

                if len(week_data) > 0:
                    table_data = [['Monday', 'Tuesday', 'Wednesday']]
                    days_in_week = {}
                    for day_name in ['Monday', 'Tuesday', 'Wednesday']:
                        day_df = week_data[week_data['day_of_week'] == day_name]
                        days_in_week[day_name] = day_df.iloc[0] if not day_df.empty else None

                    mon_date = days_in_week['Monday']['date_parsed'].strftime('%b %d') if days_in_week[
                                                                                              'Monday'] is not None else 'N/A'
                    tue_date = days_in_week['Tuesday']['date_parsed'].strftime('%b %d') if days_in_week[
                                                                                               'Tuesday'] is not None else 'N/A'
                    wed_date = days_in_week['Wednesday']['date_parsed'].strftime('%b %d') if days_in_week[
                                                                                                 'Wednesday'] is not None else 'N/A'

                    table_data[0] = [f"Monday {mon_date}", f"Tuesday {tue_date}", f"Wednesday {wed_date}"]

                    cell_styles = []

                    for i in range(3):
                        row_list = []
                        for c_idx, day in enumerate(['Monday', 'Tuesday', 'Wednesday']):
                            day_data = days_in_week[day]

                            if day_data is None or pd.isna(day_data.get('main_room_1')):
                                row_list.append('NO LUNCH')
                                cell_styles.append(('BACKGROUND', (c_idx, i + 1), (c_idx, i + 1),
                                                    colors.HexColor(ScheduleConfig.NO_LUNCH_GRAY)))
                            else:
                                if i == 2:
                                    staff = day_data['quiet_room']
                                    cell_styles.append(('BACKGROUND', (c_idx, i + 1), (c_idx, i + 1),
                                                        colors.HexColor(ScheduleConfig.PINK)))
                                else:
                                    staff = day_data['main_room_1'] if i == 0 else day_data['main_room_2']
                                    cell_styles.append(('BACKGROUND', (c_idx, i + 1), (c_idx, i + 1), colors.white))
                                row_list.append(staff if staff != 'UNASSIGNED' else '')
                        table_data.append(row_list)

                    table = Table(table_data, colWidths=[ScheduleConfig.PDF_COL_WIDTH] * 3)

                    table_style_base = [
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(ScheduleConfig.MAROON)),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 11),
                        ('FONTSIZE', (0, 1), (-1, -1), 10),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                        ('GRID', (0, 0), (-1, -1), 1, colors.black),
                        ('HEIGHT', (0, 0), (-1, -1), ScheduleConfig.PDF_ROW_HEIGHT),
                    ]

                    table_style_base.extend(cell_styles)
                    table.setStyle(TableStyle(table_style_base))

                    elements.append(table)
                    elements.append(Spacer(1, 0.2 * inch))

            legend_items = [
                Paragraph("■ Pink = Quiet Lunch Room", legend_style),
                Paragraph("■ Gray = No Lunch Duty", legend_style)
            ]

            elements.extend(legend_items)
            elements.append(PageBreak())

    else:
        title_text = f"{month_name} {year} - Lunch Duty Schedule"
        title = Paragraph(title_text, main_title_style)
        elements.append(title)

        weeks = merged_df['week'].unique()

        for week_num in sorted(weeks):
            week_data = merged_df[merged_df['week'] == week_num]

            if len(week_data) > 0:
                table_data = [['Monday', 'Tuesday', 'Wednesday']]
                days_in_week = {}
                for day_name in ['Monday', 'Tuesday', 'Wednesday']:
                    day_df = week_data[week_data['day_of_week'] == day_name]
                    days_in_week[day_name] = day_df.iloc[0] if not day_df.empty else None

                mon_date = days_in_week['Monday']['date_parsed'].strftime('%b %d') if days_in_week[
                                                                                          'Monday'] is not None else 'N/A'
                tue_date = days_in_week['Tuesday']['date_parsed'].strftime('%b %d') if days_in_week[
                                                                                           'Tuesday'] is not None else 'N/A'
                wed_date = days_in_week['Wednesday']['date_parsed'].strftime('%b %d') if days_in_week[
                                                                                             'Wednesday'] is not None else 'N/A'

                table_data[0] = [f"Monday {mon_date}", f"Tuesday {tue_date}", f"Wednesday {wed_date}"]

                cell_styles = []

                for i in range(3):
                    row_list = []
                    for c_idx, day in enumerate(['Monday', 'Tuesday', 'Wednesday']):
                        day_data = days_in_week[day]

                        if day_data is None or pd.isna(day_data.get('main_room_1')):
                            row_list.append('NO LUNCH')
                            cell_styles.append(('BACKGROUND', (c_idx, i + 1), (c_idx, i + 1),
                                                colors.HexColor(ScheduleConfig.NO_LUNCH_GRAY)))
                        else:
                            if i == 2:
                                staff = day_data['quiet_room']
                                cell_styles.append(('BACKGROUND', (c_idx, i + 1), (c_idx, i + 1),
                                                    colors.HexColor(ScheduleConfig.PINK)))
                            else:
                                staff = day_data['main_room_1'] if i == 0 else day_data['main_room_2']
                                cell_styles.append(('BACKGROUND', (c_idx, i + 1), (c_idx, i + 1), colors.white))
                            row_list.append(staff if staff != 'UNASSIGNED' else '')
                    table_data.append(row_list)

                table = Table(table_data, colWidths=[ScheduleConfig.PDF_COL_WIDTH] * 3)

                table_style_base = [
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(ScheduleConfig.MAROON)),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 11),
                    ('FONTSIZE', (0, 1), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black),
                    ('HEIGHT', (0, 0), (-1, -1), ScheduleConfig.PDF_ROW_HEIGHT),
                ]

                table_style_base.extend(cell_styles)
                table.setStyle(TableStyle(table_style_base))

                elements.append(table)
                elements.append(Spacer(1, 0.2 * inch))

        legend_items = [
            Paragraph("■ Pink = Quiet Lunch Room", legend_style),
            Paragraph("■ Gray = No Lunch Duty", legend_style)
        ]
        elements.extend(legend_items)

    doc.build(elements)
    buffer.seek(0)
    return buffer


# ==================== PNG EXPORT ====================

def create_single_png_schedule(schedule_df, period_calendar_df, month_name, year):
    """Create a clean PNG image for a single month's schedule"""
    merged_df = merge_calendar_with_schedule(period_calendar_df, schedule_df)

    merged_df['main_room_1'] = merged_df['main_room_1'].fillna('NO LUNCH')
    merged_df['main_room_2'] = merged_df['main_room_2'].fillna('NO LUNCH')
    merged_df['quiet_room'] = merged_df['quiet_room'].fillna('NO LUNCH')

    merged_df = merged_df.sort_values('date_parsed').reset_index(drop=True)

    merged_df['week_num'] = merged_df['date_parsed'].dt.isocalendar().week
    weeks = []
    for week_num in sorted(merged_df['week_num'].unique()):
        weeks.append(merged_df[merged_df['week_num'] == week_num])

    cols = 3
    rows_per_week = 4
    total_weeks = len(weeks)

    img_width = cols * ScheduleConfig.CELL_WIDTH + 2 * ScheduleConfig.PADDING
    img_height = ScheduleConfig.TITLE_HEIGHT + (total_weeks * (
            rows_per_week * ScheduleConfig.CELL_HEIGHT + ScheduleConfig.WEEK_SPACING)) + ScheduleConfig.PADDING

    img = Image.new('RGB', (img_width, img_height), 'white')
    draw = ImageDraw.Draw(img)

    fonts = load_fonts()

    title = f"{month_name} {year} - Lunch Duty Schedule"
    title_bbox = draw.textbbox((0, 0), title, font=fonts['title'])
    title_width = title_bbox[2] - title_bbox[0]
    draw.text(((img_width - title_width) // 2, ScheduleConfig.PADDING + 10), title, fill=ScheduleConfig.MAROON,
              font=fonts['title'])

    y = ScheduleConfig.TITLE_HEIGHT + ScheduleConfig.PADDING

    day_names = ['Monday', 'Tuesday', 'Wednesday']

    for week_data in weeks:
        draw.rectangle(
            [ScheduleConfig.PADDING, y, img_width - ScheduleConfig.PADDING, y + ScheduleConfig.HEADER_HEIGHT],
            fill=ScheduleConfig.MAROON, outline=ScheduleConfig.BORDER, width=2)

        headers = ['Day', 'Cafeteria', 'Quiet Room']
        for col_idx, header in enumerate(headers):
            x = ScheduleConfig.PADDING + col_idx * ScheduleConfig.CELL_WIDTH
            header_bbox = draw.textbbox((0, 0), header, font=fonts['header'])
            header_width = header_bbox[2] - header_bbox[0]
            header_height_text = header_bbox[3] - header_bbox[1]
            draw.text(
                (x + (ScheduleConfig.CELL_WIDTH - header_width) // 2,
                 y + (ScheduleConfig.HEADER_HEIGHT - header_height_text) // 2),
                header, fill='white', font=fonts['header']
            )

        y += ScheduleConfig.HEADER_HEIGHT

        for day_idx in range(3):
            day_name = day_names[day_idx]
            row_data_series = week_data[week_data['day_of_week'] == day_name]

            if not row_data_series.empty:
                row_data = row_data_series.iloc[0]
                day_date = row_data['date_parsed'].strftime('%b %d')
                main_1 = row_data['main_room_1']
                main_2 = row_data['main_room_2']
                quiet = row_data['quiet_room']
                is_no_lunch = (main_1 == 'NO LUNCH')
            else:
                day_date = ''
                main_1 = main_2 = quiet = ' '
                is_no_lunch = True

            day_text = f"{day_names[day_idx]}\n{day_date}"

            if is_no_lunch and day_date == '':
                row_bg = 'white'
                cafeteria_text = ' '
                quiet_text = ' '
            elif is_no_lunch:
                row_bg = ScheduleConfig.NO_LUNCH_GRAY
                cafeteria_text = 'NO LUNCH'
                quiet_text = 'NO LUNCH'
            else:
                row_bg = ScheduleConfig.LIGHT_GRAY if day_idx % 2 == 0 else 'white'
                cafeteria_text = f"{main_1}\n{main_2}"
                quiet_text = quiet

            draw.rectangle([ScheduleConfig.PADDING, y, ScheduleConfig.PADDING + ScheduleConfig.CELL_WIDTH,
                            y + ScheduleConfig.CELL_HEIGHT],
                           fill=row_bg, outline=ScheduleConfig.BORDER, width=1)
            draw.text((ScheduleConfig.PADDING + 10, y + 10), day_text, fill='black', font=fonts['cell'])

            draw.rectangle([ScheduleConfig.PADDING + ScheduleConfig.CELL_WIDTH, y,
                            ScheduleConfig.PADDING + 2 * ScheduleConfig.CELL_WIDTH, y + ScheduleConfig.CELL_HEIGHT],
                           fill=row_bg, outline=ScheduleConfig.BORDER, width=1)
            lines = cafeteria_text.split('\n')
            line_height_bbox = draw.textbbox((0, 0), "Tg", font=fonts['cell'])
            line_height = (line_height_bbox[3] - line_height_bbox[1]) + 4
            start_y = y + (ScheduleConfig.CELL_HEIGHT - len(lines) * line_height) // 2
            for i, line in enumerate(lines):
                text_bbox = draw.textbbox((0, 0), line, font=fonts['cell'])
                text_width = text_bbox[2] - text_bbox[0]
                draw.text(
                    (ScheduleConfig.PADDING + ScheduleConfig.CELL_WIDTH + (ScheduleConfig.CELL_WIDTH - text_width) // 2,
                     start_y + i * line_height),
                    line, fill='black', font=fonts['cell'])

            quiet_bg = ScheduleConfig.PINK if not is_no_lunch else row_bg
            draw.rectangle([ScheduleConfig.PADDING + 2 * ScheduleConfig.CELL_WIDTH, y,
                            ScheduleConfig.PADDING + 3 * ScheduleConfig.CELL_WIDTH, y + ScheduleConfig.CELL_HEIGHT],
                           fill=quiet_bg, outline=ScheduleConfig.BORDER, width=1)
            text_bbox = draw.textbbox((0, 0), quiet_text, font=fonts['cell'])
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            draw.text(
                (ScheduleConfig.PADDING + 2 * ScheduleConfig.CELL_WIDTH + (ScheduleConfig.CELL_WIDTH - text_width) // 2,
                 y + (ScheduleConfig.CELL_HEIGHT - text_height) // 2),
                quiet_text, fill='black', font=fonts['cell'])

            y += ScheduleConfig.CELL_HEIGHT

        y += ScheduleConfig.WEEK_SPACING

    buf = BytesIO()
    img.save(buf, format='PNG', quality=95)
    buf.seek(0)

    return buf


def create_png_zip_schedule(schedule_df, period_calendar_df):
    """Creates a Zip file containing one PNG per month"""
    zip_buffer = BytesIO()

    merged_df = merge_calendar_with_schedule(period_calendar_df, schedule_df)
    merged_df['month'] = merged_df['date_parsed'].dt.month
    merged_df['year'] = merged_df['date_parsed'].dt.year

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        unique_months = merged_df[['year', 'month']].drop_duplicates().sort_values(['year', 'month'])

        for _, row in unique_months.iterrows():
            current_year = row['year']
            current_month = row['month']

            month_calendar_df = period_calendar_df[
                (period_calendar_df['date_parsed'].dt.year == current_year) &
                (period_calendar_df['date_parsed'].dt.month == current_month)
                ]
            month_schedule_df = schedule_df[
                (schedule_df['date_parsed'].dt.year == current_year) &
                (schedule_df['date_parsed'].dt.month == current_month)
                ]

            if len(month_calendar_df) == 0:
                continue

            month_name_str = month_calendar_df['date_parsed'].iloc[0].strftime('%B')

            png_buffer = create_single_png_schedule(
                month_schedule_df,
                month_calendar_df,
                month_name_str,
                current_year
            )

            png_filename = f"Spire_Lunch_Duty_{current_year}_{current_month:02d}_{month_name_str}.png"
            zip_file.writestr(png_filename, png_buffer.getvalue())

    zip_buffer.seek(0)
    return zip_buffer


def create_export_bundle(schedule_df, period_calendar_df, summary_df, month_name, year, base_filename):
    """Create a zip file containing all export formats"""
    bundle_buffer = BytesIO()

    with zipfile.ZipFile(bundle_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # CSV - Schedule
        display_schedule_csv = schedule_df.drop(columns=['date_parsed'], errors='ignore')
        display_schedule_csv.columns = ['Date', 'Day', 'Main Room 1', 'Main Room 2', 'Quiet Room']
        schedule_csv_data = display_schedule_csv.to_csv(index=False)
        zip_file.writestr(f"{base_filename}_Schedule.csv", schedule_csv_data)

        # CSV - Summary
        summary_csv = summary_df.to_csv(index=False)
        zip_file.writestr(f"{base_filename}_Summary.csv", summary_csv)

        # PDF
        pdf_buffer = create_pdf_schedule(schedule_df, period_calendar_df, month_name, year)
        zip_file.writestr(f"{base_filename}_Schedule.pdf", pdf_buffer.getvalue())

        # PNG(s)
        if month_name == "Full Year":
            png_zip_buffer = create_png_zip_schedule(schedule_df, period_calendar_df)
            with zipfile.ZipFile(png_zip_buffer, 'r') as inner_zip:
                for name in inner_zip.namelist():
                    zip_file.writestr(name, inner_zip.read(name))
        else:
            png_buffer = create_single_png_schedule(schedule_df, period_calendar_df, month_name, year)
            zip_file.writestr(f"{base_filename}_Schedule.png", png_buffer.getvalue())

    bundle_buffer.seek(0)
    return bundle_buffer


@st.cache_data
def load_calendar_data(uploaded_file):
    """Loads and caches the calendar CSV to prevent re-reading on every rerun."""
    uploaded_file.seek(0)
    calendar_df = pd.read_csv(uploaded_file)
    try:
        calendar_df['date_parsed'] = pd.to_datetime(calendar_df['date'], format='%A, %B %d, %Y')
    except Exception as e:
        st.error(f"❌ Error parsing dates in calendar. Expected format: 'Monday, August 25, 2025'\nError: {str(e)}")
        return None, None

    calendar_df = calendar_df.dropna(subset=['date_parsed'])
    calendar_df['year'] = calendar_df['date_parsed'].dt.year
    calendar_df['month'] = calendar_df['date_parsed'].dt.month
    calendar_df['month_name'] = calendar_df['date_parsed'].dt.strftime('%B')

    available_months_temp = calendar_df[['year', 'month', 'month_name']].drop_duplicates().sort_values(
        ['year', 'month'])

    month_options = [f"{row['month_name']} {row['year']}" for _, row in available_months_temp.iterrows()]

    return month_options, calendar_df


# ==================== SIDEBAR INPUT ====================
st.sidebar.header("📋 Configuration")

calendar_file = st.sidebar.file_uploader("Upload Calendar CSV", type=['csv'],
                                         help="Tidy calendar with date, day_of_week, needs_duty columns")
staff_file = st.sidebar.file_uploader("Upload Staff Availability CSV", type=['csv'],
                                      help="Staff names, availability, and optional pairing constraints")

use_seed = st.sidebar.checkbox("Use fixed random seed (uncheck for random results each time)", value=True)
if use_seed:
    seed_value = st.sidebar.number_input("Random Seed", min_value=0, max_value=9999, value=42, step=1)
else:
    seed_value = None

st.sidebar.markdown("---")
st.sidebar.subheader("📅 Schedule Period")

period_mode = st.sidebar.radio(
    "Select how to define the schedule period:",
    options=["Full Year", "Specific Month", "Custom Date Range"],
    index=0,
    horizontal=False,
    key="period_mode"
)

selected_month = None
custom_start_date = None
custom_end_date = None

if period_mode == "Full Year":
    selected_month = None
    custom_start_date = None
    custom_end_date = None
    st.sidebar.info("Will schedule all available duty days in the uploaded calendar.")

elif period_mode == "Specific Month":
    custom_start_date = None
    custom_end_date = None
    if calendar_file:
        month_options, _ = load_calendar_data(calendar_file)
        if month_options is not None:
            selected_month = st.sidebar.selectbox("Select Month", month_options)
        else:
            st.sidebar.error("Error loading calendar. Check format.")
            selected_month = None
    else:
        st.sidebar.info("Upload calendar to see available months")
        selected_month = None

elif period_mode == "Custom Date Range":
    selected_month = None
    if calendar_file:
        _, temp_cal = load_calendar_data(calendar_file)
        if temp_cal is not None:
            min_cal_date = temp_cal['date_parsed'].min().date()
            max_cal_date = temp_cal['date_parsed'].max().date()

            date_range = st.sidebar.date_input(
                "Select start and end dates",
                value=(min_cal_date, max_cal_date),
                min_value=min_cal_date,
                max_value=max_cal_date,
                format="MM/DD/YYYY",
                key="custom_date_range"
            )

            if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
                custom_start_date, custom_end_date = date_range
                st.sidebar.success(
                    f"Range: {custom_start_date.strftime('%b %d, %Y')} → {custom_end_date.strftime('%b %d, %Y')}")
            else:
                st.sidebar.info("👆 Select both a start and end date above")
        else:
            st.sidebar.error("Error loading calendar.")
    else:
        st.sidebar.info("Upload calendar to enable date range selection")

st.sidebar.markdown("---")
if calendar_file:
    try:
        _, temp_cal = load_calendar_data(calendar_file)
        if temp_cal is not None and not temp_cal.empty:
            min_yr = temp_cal['date_parsed'].dt.year.min()
            max_yr = temp_cal['date_parsed'].dt.year.max()
            yr_display = str(min_yr) if min_yr == max_yr else f"{min_yr}-{max_yr}"
            st.sidebar.markdown(f"**Schedule for {yr_display} Academic Year**")
        else:
            st.sidebar.markdown("**Spire School Scheduler**")
    except Exception:
        st.sidebar.markdown("**Spire School Scheduler**")
else:
    st.sidebar.markdown("**Spire School Scheduler**")

# ==================== MAIN LOGIC ====================

if calendar_file and staff_file:
    try:
        _, calendar_df = load_calendar_data(calendar_file)

        if calendar_df is None:
            st.error("Failed to process calendar file. Please check the file and re-upload.")
            st.stop()

        staff_df = pd.read_csv(staff_file)

        required_calendar_cols = ['date', 'day_of_week', 'needs_duty']
        missing_calendar_cols = [col for col in required_calendar_cols if col not in calendar_df.columns]
        if missing_calendar_cols:
            st.error(f"❌ Calendar CSV missing required columns: {', '.join(missing_calendar_cols)}")
            st.stop()

        if 'name' in staff_df.columns:
            pass
        elif 'Name' in staff_df.columns:
            staff_df = staff_df.rename(columns={'Name': 'name'})
        elif 'Unnamed: 0' in staff_df.columns:
            staff_df = staff_df.rename(columns={'Unnamed: 0': 'name'})
        else:
            st.error("❌ Staff CSV must have a 'name' or 'Name' column (or be saved with an index). None was found.")
            st.stop()

        if not staff_df['name'].is_unique:
            st.error(f"❌ Staff CSV contains duplicate names. Please ensure every name is unique.")
            st.stop()

        required_staff_cols = ['name', 'Monday', 'Tuesday', 'Wednesday']
        missing_staff_cols = [col for col in required_staff_cols if col not in staff_df.columns]
        if missing_staff_cols:
            st.error(
                f"❌ Staff CSV missing required columns: {', '.join(missing_staff_cols)}. Found: {', '.join(staff_df.columns)}")
            st.stop()

        if 'should_not_be_paired_with_others_with_this_tag' not in staff_df.columns:
            staff_df['should_not_be_paired_with_others_with_this_tag'] = 0
        if 'should_TRY_TO_pair_with_others_with_this_tag' not in staff_df.columns:
            staff_df['should_TRY_TO_pair_with_others_with_this_tag'] = 0

        for day_col in ['Monday', 'Tuesday', 'Wednesday']:
            invalid_values = ~staff_df[day_col].isin([0, 1])
            if invalid_values.any():
                st.error(
                    f"❌ Staff availability for {day_col} must be 0 or 1 only. Check rows: {staff_df[invalid_values]['name'].tolist()}")
                st.stop()

        for tag_col in ['should_not_be_paired_with_others_with_this_tag',
                        'should_TRY_TO_pair_with_others_with_this_tag']:
            invalid_values = ~staff_df[tag_col].isin([0, 1])
            if invalid_values.any():
                st.error(f"❌ '{tag_col}' column must be 0 or 1 only.")
                st.stop()

        all_days_for_period = calendar_df[
            (calendar_df['day_of_week'] == 'Monday') |
            (calendar_df['day_of_week'] == 'Tuesday') |
            (calendar_df['day_of_week'] == 'Wednesday')
            ].copy()

        duty_days_all = calendar_df[calendar_df['needs_duty'] == 1].copy()

        if len(duty_days_all) == 0:
            st.error("❌ No duty days found in calendar (needs_duty = 1)")
            st.stop()

        if period_mode == "Specific Month" and selected_month:
            selected_parts = selected_month.split()
            month_name_selected = selected_parts[0]
            year = int(selected_parts[1])
            month = pd.to_datetime(f"{month_name_selected} 1, {year}").month

            duty_days_to_schedule = duty_days_all[
                (duty_days_all['date_parsed'].dt.year == year) &
                (duty_days_all['date_parsed'].dt.month == month)
                ].copy()

            period_calendar_df = all_days_for_period[
                (all_days_for_period['date_parsed'].dt.year == year) &
                (all_days_for_period['date_parsed'].dt.month == month)
                ].copy()

            current_period_name = selected_month
            current_year_val = year
            current_month_name = selected_month.split()[0]

        elif period_mode == "Custom Date Range" and custom_start_date and custom_end_date:
            duty_days_to_schedule = duty_days_all[
                (duty_days_all['date_parsed'].dt.date >= custom_start_date) &
                (duty_days_all['date_parsed'].dt.date <= custom_end_date)
                ].copy()

            period_calendar_df = all_days_for_period[
                (all_days_for_period['date_parsed'].dt.date >= custom_start_date) &
                (all_days_for_period['date_parsed'].dt.date <= custom_end_date)
                ].copy()

            current_period_name = f"{custom_start_date.strftime('%b %d, %Y')} – {custom_end_date.strftime('%b %d, %Y')}"
            current_year_val = custom_start_date.year
            current_month_name = "Custom Range"

        else:
            duty_days_to_schedule = duty_days_all
            period_calendar_df = all_days_for_period
            min_year = duty_days_all['date_parsed'].dt.year.min()
            max_year = duty_days_all['date_parsed'].dt.year.max()
            school_year_display = str(min_year) if min_year == max_year else f"{min_year}-{max_year}"
            current_period_name = f"Full {school_year_display} Academic Year"
            current_year_val = min_year
            current_month_name = "Full Year"

        if len(duty_days_to_schedule) == 0:
            st.warning(f"⚠️ No duty days found for the selected period.")
            st.stop()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📅 Total Duty Days to Schedule", len(duty_days_to_schedule))
        with col2:
            st.metric("👥 Total Staff", len(staff_df))
        with col3:
            total_slots = len(duty_days_to_schedule) * 3
            base_per_person = total_slots // len(staff_df)
            remainder = total_slots % len(staff_df)
            st.metric("📊 Duties/Person",
                      f"{base_per_person}{'+1 for ' + str(remainder) + ' staff' if remainder > 0 else ''}")

        staff_with_anti_tag_count = (staff_df['should_not_be_paired_with_others_with_this_tag'] == 1).sum()
        if staff_with_anti_tag_count > 0:
            st.info(
                f"ℹ️ **Constraint Active (Avoid Pairing):** {staff_with_anti_tag_count} staff member(s) will preferably not be scheduled together on the same day.")

        staff_with_pro_tag_count = (staff_df['should_TRY_TO_pair_with_others_with_this_tag'] == 1).sum()
        if staff_with_pro_tag_count > 0:
            st.info(
                f"ℹ️ **Constraint Active (Try to Pair):** {staff_with_pro_tag_count} staff member(s) marked to preferably work together. Pairing adjustments will be attempted after the fair schedule is built.")

        def generate_schedule():
            with st.spinner("Generating gap‑maximizing fair schedule..."):
                try:
                    schedule_df, summary_df, quotas, quota_warnings = generate_gap_maximizing_schedule(
                        duty_days_to_schedule, staff_df, seed=seed_value
                    )

                    st.session_state.quota_warnings = quota_warnings

                    schedule_df = apply_tag_swaps(
                        schedule_df, staff_df, quotas, seed=seed_value
                    )

                    staff_names = staff_df['name'].tolist()
                    duty_count = {name: 0 for name in staff_names}
                    quiet_room_count = {name: 0 for name in staff_names}

                    for _, row in schedule_df.iterrows():
                        for col in ['main_room_1', 'main_room_2', 'quiet_room']:
                            name = row[col]
                            if name != 'UNASSIGNED':
                                duty_count[name] = duty_count.get(name, 0) + 1
                                if col == 'quiet_room':
                                    quiet_room_count[name] = quiet_room_count.get(name, 0) + 1

                    summary_df = pd.DataFrame({
                        'staff_name': staff_names,
                        'target_quota': [quotas.get(name, 0) for name in staff_names],
                        'actual_duties': [duty_count.get(name, 0) for name in staff_names],
                        'quiet_room_duties': [quiet_room_count.get(name, 0) for name in staff_names],
                        'main_room_duties': [duty_count.get(name, 0) - quiet_room_count.get(name, 0) for name in
                                             staff_names]
                    })
                    summary_df = summary_df.sort_values('actual_duties', ascending=False)

                    st.session_state.schedule_df = schedule_df
                    st.session_state.summary_df = summary_df
                    st.session_state.quotas = quotas
                    st.session_state.schedule_ready = True
                    st.session_state.period_calendar_df = period_calendar_df
                    st.session_state.period_name = current_period_name
                    st.session_state.month_name = current_month_name
                    st.session_state.year_val = current_year_val

                except Exception as e:
                    st.error(f"❌ Error generating schedule: {str(e)}")
                    st.exception(e)

        st.button("🎲 Generate Schedule", type="primary", on_click=generate_schedule)

        if st.session_state.schedule_ready:
            st.success(
                f"✅ Schedule for **{st.session_state.period_name}** ready! ({len(st.session_state.schedule_df)} duty days assigned)")

            if st.session_state.quota_warnings:
                with st.expander("⚠️ Quota Adjustments (Availability Limits)", expanded=False):
                    for w in st.session_state.quota_warnings:
                        st.warning(w)

            issues = check_schedule_conflicts(st.session_state.schedule_df, staff_df)
            if issues:
                with st.expander("⚠️ Schedule Warnings", expanded=True):
                    for issue in issues:
                        st.warning(issue)

            if hasattr(st.session_state, 'quotas'):
                quotas = st.session_state.quotas
                unique_quotas = sorted(set(quotas.values()))
                quota_desc = " + ".join([f"{list(quotas.values()).count(q)}×{q}" for q in unique_quotas])
                st.info(
                    f"📐 **Quota breakdown:** Each person gets exactly {quota_desc} duties — the mathematical minimum required to cover {len(duty_days_to_schedule) * 3} total slots across {len(staff_df)} staff.")

            st.subheader("📋 Duty Schedule (Assigned Days Only)")
            display_schedule = st.session_state.schedule_df.drop(columns=['date_parsed'], errors='ignore')
            display_schedule.columns = ['Date', 'Day', 'Main Room 1', 'Main Room 2', 'Quiet Room']
            st.dataframe(display_schedule, use_container_width=True, height=400)

            st.subheader("📊 Duty Distribution Summary")
            col1, col2 = st.columns(2)

            with col1:
                st.dataframe(st.session_state.summary_df, use_container_width=True, height=300)

            with col2:
                st.markdown("**Distribution Check:**")
                if not st.session_state.summary_df.empty:
                    min_duties = st.session_state.summary_df['actual_duties'].min()
                    max_duties = st.session_state.summary_df['actual_duties'].max()
                    diff = max_duties - min_duties

                    st.metric("Min Duties", min_duties)
                    st.metric("Max Duties", max_duties)
                    st.metric("Difference (should be ≤1)", diff, delta=None if diff <= 1 else "⚠️ Unbalanced")

                    if diff <= 1:
                        st.success("✅ Everyone has their exact fair share!")
                    else:
                        st.warning("⚠️ Schedule may need adjustment")
                else:
                    st.warning("No summary data to display.")

            st.subheader("💾 Download Results")

            gen_date_str = datetime.now().strftime('%m.%d.%y')
            period_str = st.session_state.period_name
            safe_period_str = period_str.replace(" ", "_").replace("-", "_").replace("–", "_").replace(",", "")
            base_filename = f"Spire_Lunch_Duty_{safe_period_str}_gen_on_{gen_date_str}"

            batch_export = st.checkbox("📦 Export all formats at once (CSV + PDF + PNG)")

            if batch_export:
                if st.button("📥 Generate Complete Bundle", type="primary"):
                    with st.spinner("Creating complete export bundle..."):
                        bundle_buffer = create_export_bundle(
                            st.session_state.schedule_df,
                            st.session_state.period_calendar_df,
                            st.session_state.summary_df,
                            st.session_state.month_name,
                            st.session_state.year_val,
                            base_filename
                        )
                        st.download_button(
                            label="📥 Download Complete Bundle (.zip)",
                            data=bundle_buffer,
                            file_name=f"{base_filename}_Complete.zip",
                            mime="application/zip"
                        )
            else:
                export_format = st.radio("Choose export format:",
                                         ["CSV (Data)", "PDF (Print-friendly)", "PNG (Image)"],
                                         horizontal=True)

                col1, col2 = st.columns(2)

                with col1:
                    if export_format == "CSV (Data)":
                        display_schedule_csv = st.session_state.schedule_df.drop(columns=['date_parsed'],
                                                                                 errors='ignore')
                        display_schedule_csv.columns = ['Date', 'Day', 'Main Room 1', 'Main Room 2', 'Quiet Room']
                        schedule_csv_data = display_schedule_csv.to_csv(index=False)
                        st.download_button(
                            label="📥 Download Schedule CSV",
                            data=schedule_csv_data,
                            file_name=f"{base_filename}_Schedule.csv",
                            mime="text/csv"
                        )

                    elif export_format == "PDF (Print-friendly)":
                        pdf_buffer = create_pdf_schedule(
                            st.session_state.schedule_df,
                            st.session_state.period_calendar_df,
                            st.session_state.month_name,
                            st.session_state.year_val
                        )
                        st.download_button(
                            label="📥 Download Schedule PDF",
                            data=pdf_buffer,
                            file_name=f"{base_filename}_Schedule.pdf",
                            mime="application/pdf"
                        )

                    else:
                        if st.session_state.month_name == "Full Year" or st.session_state.month_name == "Custom Range":
                            zip_buffer = create_png_zip_schedule(
                                st.session_state.schedule_df,
                                st.session_state.period_calendar_df
                            )
                            st.download_button(
                                label="📥 Download All Monthly PNGs (.zip)",
                                data=zip_buffer,
                                file_name=f"{base_filename}_Monthly_Schedules.zip",
                                mime="application/zip"
                            )
                        else:
                            png_buffer = create_single_png_schedule(
                                st.session_state.schedule_df,
                                st.session_state.period_calendar_df,
                                st.session_state.month_name,
                                st.session_state.year_val
                            )
                            st.download_button(
                                label="📥 Download Schedule PNG",
                                data=png_buffer,
                                file_name=f"{base_filename}_Schedule.png",
                                mime="image/png"
                            )

                with col2:
                    summary_csv = st.session_state.summary_df.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Summary CSV",
                        data=summary_csv,
                        file_name=f"{base_filename}_Summary.csv",
                        mime="text/csv"
                    )

    except Exception as e:
        st.error(f"❌ An error occurred. Please check your files and settings.")
        st.exception(e)

else:
    st.info("👈 Please upload both Calendar and Staff CSV files to begin")

    with st.expander("ℹ️ How to use this app"):
        st.markdown("""
        ### Required Files:

        **1. Calendar CSV** - Must contain:
        - `date` - Full date string (e.g., "Monday, August 25, 2025")
        - `day_of_week` - Day name (Monday, Tuesday, Wednesday, etc.)
        - `needs_duty` - Binary flag (1 = duty needed, 0 = no duty)

        **2. Staff Availability CSV** - Must contain:
        - First column: Staff names (column header can be `name` or `Name`)
        - `Monday` - Binary (1 = available, 0 = not available)
        - `Tuesday` - Binary (1 = available, 0 = not available)
        - `Wednesday` - Binary (1 = available, 0 = not available)

        **Optional Scheduling Constraints (Binary 1/0 columns):**
        - `should_not_be_paired_with_others_with_this_tag`: Staff marked with `1` will preferably not be scheduled together on the same day.
        - `should_TRY_TO_pair_with_others_with_this_tag`: Staff marked with `1` prefer to work together. Pairing is attempted via post-processing swaps after the fair schedule is built.

        ### Algorithm (V2.0):
        - ✅ **Phase 1:** Every person gets the exact mathematical minimum number of duties (some get +1 if slots don't divide evenly).
        - ✅ **Phase 2:** Greedy gap‑maximizing assignment: each day, the 3 people with the longest gaps since their last duty are selected, with randomization to vary day‑of‑week patterns.
        - ✅ **Phase 3:** Minor post‑processing swaps to accommodate optional pairing/anti‑pairing tags without disrupting fairness.

        ### Schedule Period:
        - **Full Year:** All duty days in the uploaded calendar.
        - **Specific Month:** Pick one calendar month.
        - **Custom Date Range:** Use the calendar widget to pick any start and end date.

        ### Export Formats:
        - **CSV**: Raw data for *assigned duty days only*.
        - **PDF**: Professional print-ready schedule. *Shows all Mon/Tue/Wed. "NO LUNCH" days are gray.*
        - **PNG**: Image format. *Shows all Mon/Tue/Wed. "NO LUNCH" days are gray.*
        - **Batch**: All formats in one zip file.

        ### Tips:
        - Use a random seed for reproducible results.
        - **Important:** After changing the period, you *must* click "Generate Schedule" again before downloading.
        """)
