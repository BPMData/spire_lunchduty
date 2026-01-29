import streamlit as st
import pandas as pd
import numpy as np
import random
from datetime import datetime
import io
from io import BytesIO
import zipfile

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from PIL import Image, ImageDraw, ImageFont


st.set_page_config(page_title="Lunch Duty Scheduler", page_icon="🍽️", layout="wide")

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


st.title("🍽️ Spire School Lunch Duty Scheduler")
st.markdown("*Automated fair scheduling for Mon/Tue/Wed lunch duties*")

# ==================== SESSION STATE INITIALIZATION ====================
if "schedule_df" not in st.session_state:
    st.session_state.schedule_df = None
if "period_calendar_df" not in st.session_state:
    st.session_state.period_calendar_df = None
if "summary_df" not in st.session_state:
    st.session_state.summary_df = None
if "schedule_ready" not in st.session_state:
    st.session_state.schedule_ready = False
if "month_name" not in st.session_state:
    st.session_state.month_name = "Full Year"
if "year_val" not in st.session_state:
    st.session_state.year_val = 2025

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
    
    # Check for days with multiple staff who have the pairing constraint tag (The "Anti-Pairing" tag)
    staff_with_anti_tag = set(staff_df[staff_df['should_not_be_paired_with_others_with_this_tag'] == 1]['name'].tolist())
    
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
            issues.append(f"⚠️ {len(days_with_multiple_tagged)} day(s) have multiple staff with the 'avoid pairing' tag:")
            for day_info in days_with_multiple_tagged[:5]:  # Show first 5
                issues.append(f"  - {day_info['date']}: {', '.join(day_info['staff'])}")
            if len(days_with_multiple_tagged) > 5:
                issues.append(f"  - ...and {len(days_with_multiple_tagged) - 5} more")
    
    return issues


def generate_lunch_duty_schedule(duty_days_df, staff_df, seed=None):
    """Generate fair lunch duty schedule with all constraints"""
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    staff_names = staff_df['name'].tolist()
    duty_count = {name: 0 for name in staff_names}
    quiet_room_count = {name: 0 for name in staff_names}
    last_duty_week = {name: -10 for name in staff_names}
    
    # Track which staff have the pairing constraint tags
    # Tag 1: Avoid pairing together (Anti-Pairing)
    staff_with_anti_pairing_tag = set(staff_df[staff_df['should_not_be_paired_with_others_with_this_tag'] == 1]['name'].tolist())
    # Tag 2: Try to pair together (Pro-Pairing)
    staff_with_pro_pairing_tag = set(staff_df[staff_df['should_TRY_TO_pair_with_others_with_this_tag'] == 1]['name'].tolist())


    schedule = []
    total_slots = len(duty_days_df) * 3
    
    if len(staff_names) == 0:
        st.error("❌ Staff list is empty. Cannot generate schedule.")
        return pd.DataFrame(), pd.DataFrame()
        
    target_duties = total_slots // len(staff_names)

    for idx, row in duty_days_df.iterrows():
        date = row['date']
        date_parsed = row['date_parsed']
        day_of_week = row['day_of_week']
        week_number = date_parsed.isocalendar()[1]

        # 1. Get available staff based on day of week and weekly limit constraint
        available_staff = []
        for _, staff_row in staff_df.iterrows():
            name = staff_row['name']
            if staff_row[day_of_week] == 1:
                if last_duty_week[name] != week_number:
                    # Also check against target duties to maintain balance early on
                    if duty_count[name] <= target_duties:
                        available_staff.append(name)

        # Relax constraints if needed (if not enough people available)
        if len(available_staff) < 3:
            available_staff = [
                staff_row['name'] for _, staff_row in staff_df.iterrows() 
                if staff_row[day_of_week] == 1 and duty_count[staff_row['name']] <= target_duties
            ]

        if len(available_staff) < 3:
            available_staff = [
                staff_row['name'] for _, staff_row in staff_df.iterrows() 
                if staff_row[day_of_week] == 1
            ]

        # Sort available staff by duty count (for fairness)
        available_staff.sort(key=lambda x: (duty_count[x], quiet_room_count[x]))
        
        selected_staff = []
        
        # --- NEW LOGIC: Multi-pass Selection ---

        # PASS 1: Try to satisfy the "Pro-Pairing" requirement first.
        # If there are at least 2 available staff with the pro-pairing tag, prioritize picking them together.
        available_pro_pairers = [s for s in available_staff if s in staff_with_pro_pairing_tag]
        
        if len(available_pro_pairers) >= 2:
            # We have enough to make a pair.
            # Iterate through the sorted pro-pairers and pick the first two that don't violate anti-pairing constraints.
            temp_pro_picks = []
            anti_paired_count_in_temp = 0

            for staff in available_pro_pairers:
                if len(temp_pro_picks) == 2: break

                is_anti_paired = staff in staff_with_anti_pairing_tag
                
                # If picking this person violates the anti-pairing constraint (max 1 per day), skip them for now.
                if is_anti_paired and anti_paired_count_in_temp >= 1:
                    continue

                temp_pro_picks.append(staff)
                if is_anti_paired:
                    anti_paired_count_in_temp += 1
            
            # Only commit to these picks if we successfully got at least 2 to form a "pair"
            if len(temp_pro_picks) >= 2:
                 selected_staff.extend(temp_pro_picks)

        # PASS 2: Fill remaining slots, respecting the "Anti-Pairing" constraint.
        
        # Calculate current anti-pairing count based on Pass 1 picks
        anti_pairing_count = sum(1 for s in selected_staff if s in staff_with_anti_pairing_tag)
        max_anti_paired_allowed = 1 # Only allow 1 staff member with the tag per day
        
        for staff in available_staff:
            if len(selected_staff) >= 3:
                break
            
            if staff in selected_staff:
                continue # Already picked in Pass 1

            if staff in staff_with_anti_pairing_tag:
                if anti_pairing_count < max_anti_paired_allowed:
                    selected_staff.append(staff)
                    anti_pairing_count += 1
            else:
                selected_staff.append(staff)
        
        # If we couldn't fill slots while respecting anti-pairing constraint, relax it as a last resort
        if len(selected_staff) < 3:
            for staff in available_staff:
                if staff not in selected_staff:
                    selected_staff.append(staff)
                    if len(selected_staff) >= 3:
                        break
        
        # Fill remaining slots with UNASSIGNED if needed (absolute last resort)
        while len(selected_staff) < 3:
            selected_staff.append('UNASSIGNED')

        # Assign rooms
        random.shuffle(selected_staff)
        # Prioritize those with fewest quiet room duties for the quiet room slot
        selected_staff.sort(key=lambda x: quiet_room_count.get(x, 0) if x != 'UNASSIGNED' else 999)

        quiet_room_staff = selected_staff[0]
        main_room_staff = selected_staff[1:]

        # Update tracking
        for staff in selected_staff:
            if staff != 'UNASSIGNED':
                duty_count[staff] += 1
                last_duty_week[staff] = week_number

        if quiet_room_staff != 'UNASSIGNED':
            quiet_room_count[quiet_room_staff] += 1

        schedule.append({
            'date': date,
            'date_parsed': date_parsed,
            'day_of_week': day_of_week,
            'main_room_1': main_room_staff[0] if len(main_room_staff) > 0 else 'UNASSIGNED',
            'main_room_2': main_room_staff[1] if len(main_room_staff) > 1 else 'UNASSIGNED',
            'quiet_room': quiet_room_staff
        })

    schedule_df = pd.DataFrame(schedule)

    summary = pd.DataFrame({
        'staff_name': staff_names,
        'total_duties': [duty_count[name] for name in staff_names],
        'quiet_room_duties': [quiet_room_count[name] for name in staff_names],
        'main_room_duties': [duty_count[name] - quiet_room_count[name] for name in staff_names]
    })
    summary = summary.sort_values('total_duties', ascending=False)

    return schedule_df, summary


def create_pdf_schedule(schedule_df, period_calendar_df, month_name, year):
    """Create a nicely formatted PDF of the schedule"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), topMargin=0.3*inch, bottomMargin=0.3*inch)
    elements = []
    styles = getSampleStyleSheet()

    main_title_style = ParagraphStyle(
        'MainTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=colors.HexColor(ScheduleConfig.MAROON),
        spaceAfter=0.5*inch,
        alignment=1
    )
    month_title_style = ParagraphStyle(
        'MonthTitle',
        parent=styles['Heading2'],
        fontSize=20,
        textColor=colors.HexColor(ScheduleConfig.MAROON),
        spaceAfter=0.3*inch,
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
        # Determine school year range
        min_yr = merged_df['year'].min()
        max_yr = merged_df['year'].max()
        if min_yr == max_yr:
            year_range = str(min_yr)
        else:
            year_range = f"{min_yr}-{max_yr}"
        
        title = Paragraph("Spire School Lunch Duty Schedule", main_title_style)
        subtitle = Paragraph(f"Full {year_range} Academic Year", month_title_style)
        elements.append(title)
        elements.append(subtitle)
        elements.append(PageBreak())

        unique_months = merged_df[['year', 'month']].drop_duplicates().sort_values(['year', 'month'])
        
        for idx, row in unique_months.iterrows():
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
                        if not day_df.empty:
                            days_in_week[day_name] = day_df.iloc[0]
                        else:
                            days_in_week[day_name] = None
                    
                    mon_date = days_in_week['Monday']['date_parsed'].strftime('%b %d') if days_in_week['Monday'] is not None else 'N/A'
                    tue_date = days_in_week['Tuesday']['date_parsed'].strftime('%b %d') if days_in_week['Tuesday'] is not None else 'N/A'
                    wed_date = days_in_week['Wednesday']['date_parsed'].strftime('%b %d') if days_in_week['Wednesday'] is not None else 'N/A'

                    table_data[0] = [f"Monday {mon_date}", f"Tuesday {tue_date}", f"Wednesday {wed_date}"]
                    
                    cell_styles = [] 
                    
                    for i in range(3):
                        row_list = []
                        for c_idx, day in enumerate(['Monday', 'Tuesday', 'Wednesday']):
                            day_data = days_in_week[day]
                            
                            if day_data is None or pd.isna(day_data.get('main_room_1')):
                                row_list.append('NO LUNCH')
                                cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.HexColor(ScheduleConfig.NO_LUNCH_GRAY)))
                            else:
                                if i == 2:
                                    staff = day_data['quiet_room']
                                    cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.HexColor(ScheduleConfig.PINK)))
                                else:
                                    staff = day_data['main_room_1'] if i == 0 else day_data['main_room_2']
                                    cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.white))
                                row
