import streamlit as st
import pandas as pd
import numpy as np
import random
from datetime import datetime
import io
from io import BytesIO
import zipfile  # For zipping PNGs

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from PIL import Image, ImageDraw, ImageFont


st.set_page_config(page_title="Lunch Duty Scheduler", page_icon="🍽️", layout="wide")

st.title("🍽️ Spire School Lunch Duty Scheduler")
st.markdown("*Automated fair scheduling for Mon/Tue/Wed lunch duties*")

# ==================== SESSION STATE INITIALIZATION ====================
if "schedule_df" not in st.session_state:
    st.session_state.schedule_df = None # DF with only duty days + assignments
if "period_calendar_df" not in st.session_state:
    st.session_state.period_calendar_df = None # DF with ALL Mon/Tue/Wed for the period
if "summary_df" not in st.session_state:
    st.session_state.summary_df = None
if "schedule_ready" not in st.session_state:
    st.session_state.schedule_ready = False
if "month_name" not in st.session_state:
    st.session_state.month_name = "Full Year"
if "year_val" not in st.session_state:
    st.session_state.year_val = 2025

# ==================== HELPER FUNCTIONS ====================

def generate_lunch_duty_schedule(duty_days_df, staff_df, seed=None):
    """Generate fair lunch duty schedule with all constraints"""
    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    staff_names = staff_df['name'].tolist()
    duty_count = {name: 0 for name in staff_names}
    quiet_room_count = {name: 0 for name in staff_names}
    last_duty_week = {name: -10 for name in staff_names}

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

        # Get available staff
        available_staff = []
        for _, staff_row in staff_df.iterrows():
            name = staff_row['name']
            if staff_row[day_of_week] == 1:
                if last_duty_week[name] != week_number:
                    if duty_count[name] <= target_duties:
                        available_staff.append(name)

        # Relax constraints if needed
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

        # Sort and select
        available_staff.sort(key=lambda x: (duty_count[x], quiet_room_count[x]))
        selected_staff = available_staff[:3] if len(available_staff) >= 3 else available_staff + ['UNASSIGNED'] * (3 - len(available_staff))

        random.shuffle(selected_staff)
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
            'day_of_week': day_of_week, # <-- FIX 1: Renamed from 'day_of_week_duty'
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
    """
    Create a nicely formatted PDF of the schedule.
    Handles both single-month and multi-month (full year) requests.
    Uses period_calendar_df for all days, and schedule_df for duty assignments.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), topMargin=0.3*inch, bottomMargin=0.3*inch)
    elements = []
    styles = getSampleStyleSheet()

    # --- Common Styles ---
    main_title_style = ParagraphStyle(
        'MainTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=colors.HexColor('#8B0000'),
        spaceAfter=0.5*inch,
        alignment=1
    )
    month_title_style = ParagraphStyle(
        'MonthTitle',
        parent=styles['Heading2'],
        fontSize=20,
        textColor=colors.HexColor('#8B0000'),
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
    
    # Merge all days with duty days
    period_calendar_df['date_parsed'] = pd.to_datetime(period_calendar_df['date_parsed'])
    schedule_df['date_parsed'] = pd.to_datetime(schedule_df['date_parsed'])

    merged_df = period_calendar_df.merge(
        schedule_df.drop(columns=['date'], errors='ignore'),
        on=['date_parsed', 'day_of_week'], # <-- FIX 2: Join on both keys
        how='left'
    )

    merged_df['week'] = merged_df['date_parsed'].dt.isocalendar().week
    merged_df['month'] = merged_df['date_parsed'].dt.month
    merged_df['year'] = merged_df['date_parsed'].dt.year

    # --- Logic for Full Year (Multi-Month) ---
    if month_name == "Full Year":
        title = Paragraph("Spire School Lunch Duty Schedule", main_title_style)
        subtitle = Paragraph("Full 2025-2026 Academic Year", month_title_style)
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
                    
                    for i in range(3): # For rows: Main 1, Main 2, Quiet
                        row_list = []
                        for c_idx, day in enumerate(['Monday', 'Tuesday', 'Wednesday']):
                            day_data = days_in_week[day]
                            
                            if day_data is None or pd.isna(day_data.get('main_room_1')):
                                row_list.append('NO LUNCH')
                                cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.HexColor('#E0E0E0')))
                            else:
                                if i == 2:
                                    staff = day_data['quiet_room']
                                    cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.HexColor('#FFB6D9')))
                                else:
                                    staff = day_data['main_room_1'] if i == 0 else day_data['main_room_2']
                                    cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.white))
                                row_list.append(staff if staff != 'UNASSIGNED' else '')
                        table_data.append(row_list)

                    table = Table(table_data, colWidths=[2.5*inch, 2.5*inch, 2.5*inch])
                    
                    table_style_base = [
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#8B0000')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 11),
                        ('FONTSIZE', (0, 1), (-1, -1), 10),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                        ('GRID', (0, 0), (-1, -1), 1, colors.black),
                        ('HEIGHT', (0, 0), (-1, -1), 0.5*inch),
                    ]
                    
                    table_style_base.extend(cell_styles) 
                    table.setStyle(TableStyle(table_style_base))

                    elements.append(table)
                    elements.append(Spacer(1, 0.2*inch)) 

            legend_items = [
                Paragraph("■ Pink = Quiet Lunch Room", legend_style),
                Paragraph("■ Gray = No Lunch Duty", legend_style)
            ]
            
            elements.extend(legend_items)
            elements.append(PageBreak())

    # --- Logic for Single Month ---
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
                            cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.HexColor('#E0E0E0')))
                        else:
                            if i == 2:
                                staff = day_data['quiet_room']
                                cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.HexColor('#FFB6D9')))
                            else:
                                staff = day_data['main_room_1'] if i == 0 else day_data['main_room_2']
                                cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.white))
                            row_list.append(staff if staff != 'UNASSIGNED' else '')
                    table_data.append(row_list)
                
                table = Table(table_data, colWidths=[2.5*inch, 2.5*inch, 2.5*inch])
                
                table_style_base = [
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#8B0000')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 11),
                    ('FONTSIZE', (0, 1), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black),
                    ('HEIGHT', (0, 0), (-1, -1), 0.5*inch), 
                ]
                
                table_style_base.extend(cell_styles)
                table.setStyle(TableStyle(table_style_base))

                elements.append(table)
                elements.append(Spacer(1, 0.2*inch)) 

        legend_items = [
            Paragraph("■ Pink = Quiet Lunch Room", legend_style),
            Paragraph("■ Gray = No Lunch Duty", legend_style)
        ]
        elements.extend(legend_items)

    # Build the PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer


def create_single_png_schedule(schedule_df, period_calendar_df, month_name, year):
    """
    Create a clean PNG image for a SINGLE month's schedule using Pillow.
    Uses period_calendar_df to show ALL days, not just duty days.
    """
    # Merge all days with duty days
    period_calendar_df['date_parsed'] = pd.to_datetime(period_calendar_df['date_parsed'])
    schedule_df['date_parsed'] = pd.to_datetime(schedule_df['date_parsed'])

    merged_df = period_calendar_df.merge(
        schedule_df.drop(columns=['date'], errors='ignore'), 
        on=['date_parsed', 'day_of_week'], # This join is now correct
        how='left',
        suffixes=('', '_duty')
    )
    
    # Fill NaNs for non-duty days
    merged_df['main_room_1'] = merged_df['main_room_1'].fillna('NO LUNCH')
    merged_df['main_room_2'] = merged_df['main_room_2'].fillna('NO LUNCH')
    merged_df['quiet_room'] = merged_df['quiet_room'].fillna('NO LUNCH')
    
    merged_df = merged_df.sort_values('date_parsed').reset_index(drop=True)
    
    # Group into weeks
    merged_df['week_num'] = merged_df['date_parsed'].dt.isocalendar().week
    weeks = []
    for week_num in sorted(merged_df['week_num'].unique()):
        weeks.append(merged_df[merged_df['week_num'] == week_num])

    # Image dimensions
    cell_width = 250
    cell_height = 60
    header_height = 60
    title_height = 80
    padding = 20
    week_spacing = 30
    
    cols = 3  # Day label + Cafeteria + Quiet Room
    rows_per_week = 4  # Header + Mon + Tue + Wed
    total_weeks = len(weeks)
    
    img_width = cols * cell_width + 2 * padding
    img_height = title_height + (total_weeks * (rows_per_week * cell_height + week_spacing)) + padding
    
    img = Image.new('RGB', (img_width, img_height), 'white')
    draw = ImageDraw.Draw(img)
    
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 32)
        header_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 16)
        cell_font = ImageFont.truetype("DejaVuSans.ttf", 14)
    except IOError:
        try:
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
            header_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            cell_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except IOError:
            title_font = ImageFont.load_default()
            header_font = ImageFont.load_default()
            cell_font = ImageFont.load_default()
    
    maroon = '#8B0000'
    pink = '#FFB6D9'
    light_gray = '#F5F5F5' 
    no_lunch_gray = '#E0E0E0' 
    border = '#CCCCCC'
    
    title = f"{month_name} {year} - Lunch Duty Schedule"
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    draw.text(((img_width - title_width) // 2, padding + 10), title, fill=maroon, font=title_font)
    
    y = title_height + padding
    
    day_names = ['Monday', 'Tuesday', 'Wednesday']
    
    # Draw each week
    for week_data in weeks: # week_data is now a DataFrame
        draw.rectangle([padding, y, img_width - padding, y + header_height], fill=maroon, outline=border, width=2)
        
        headers = ['Day', 'Cafeteria', 'Quiet Room']
        for col_idx, header in enumerate(headers):
            x = padding + col_idx * cell_width
            header_bbox = draw.textbbox((0, 0), header, font=header_font)
            header_width = header_bbox[2] - header_bbox[0]
            header_height_text = header_bbox[3] - header_bbox[1]
            draw.text(
                (x + (cell_width - header_width) // 2, y + (header_height - header_height_text) // 2),
                header, fill='white', font=header_font
            )
        
        y += header_height
        
        # Draw days in the week
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
                row_bg = no_lunch_gray 
                cafeteria_text = 'NO LUNCH'
                quiet_text = 'NO LUNCH'
            else:
                row_bg = light_gray if day_idx % 2 == 0 else 'white' 
                cafeteria_text = f"{main_1}\n{main_2}"
                quiet_text = quiet

            # Draw day cell
            draw.rectangle([padding, y, padding + cell_width, y + cell_height], 
                          fill=row_bg, outline=border, width=1)
            draw.text((padding + 10, y + 10), day_text, fill='black', font=cell_font)
            
            # Draw cafeteria
            draw.rectangle([padding + cell_width, y, padding + 2*cell_width, y + cell_height],
                          fill=row_bg, outline=border, width=1)
            lines = cafeteria_text.split('\n')
            line_height_bbox = draw.textbbox((0,0), "Tg", font=cell_font)
            line_height = (line_height_bbox[3] - line_height_bbox[1]) + 4
            start_y = y + (cell_height - len(lines) * line_height) // 2
            for i, line in enumerate(lines):
                text_bbox = draw.textbbox((0, 0), line, font=cell_font)
                text_width = text_bbox[2] - text_bbox[0]
                draw.text((padding + cell_width + (cell_width - text_width) // 2, 
                          start_y + i * line_height), 
                         line, fill='black', font=cell_font)
            
            # Draw quiet room
            quiet_bg = pink if not is_no_lunch else row_bg
            draw.rectangle([padding + 2*cell_width, y, padding + 3*cell_width, y + cell_height],
                          fill=quiet_bg, outline=border, width=1)
            text_bbox = draw.textbbox((0, 0), quiet_text, font=cell_font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            draw.text((padding + 2*cell_width + (cell_width - text_width) // 2,
                      y + (cell_height - text_height) // 2),
                     quiet_text, fill='black', font=cell_font)
            
            y += cell_height
        
        y += week_spacing
    
    buf = BytesIO()
    img.save(buf, format='PNG', quality=95)
    buf.seek(0)
    
    return buf


def create_png_zip_schedule(schedule_df, period_calendar_df):
    """
    Creates a Zip file in memory containing one PNG per month.
    """
    zip_buffer = BytesIO()
    
    period_calendar_df['date_parsed'] = pd.to_datetime(period_calendar_df['date_parsed'])
    schedule_df['date_parsed'] = pd.to_datetime(schedule_df['date_parsed'])
    
    merged_df = period_calendar_df.merge(
        schedule_df.drop(columns=['date'], errors='ignore'), 
        on=['date_parsed', 'day_of_week'], # <-- FIX 3: Join on both keys
        how='left',
        suffixes=('', '_duty')
    )
    
    merged_df['month'] = merged_df['date_parsed'].dt.month
    merged_df['year'] = merged_df['date_parsed'].dt.year
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        unique_months = merged_df[['year', 'month']].drop_duplicates().sort_values(['year', 'month'])
        
        for idx, row in unique_months.iterrows():
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


# ==================== SIDEBAR INPUT ====================
st.sidebar.header("📋 Configuration")

calendar_file = st.sidebar.file_uploader("Upload Calendar CSV", type=['csv'], 
                                          help="Tidy calendar with date, day_of_week, needs_duty columns")
staff_file = st.sidebar.file_uploader("Upload Staff Availability CSV", type=['csv'],
                                       help="Staff names and Mon/Tue/Wed availability (1=available, 0=not)")

use_seed = st.sidebar.checkbox("Use a random seed for generating lunch duty schedules. Leave this checked or enter your own number below for reproducible results. Uncheck for random results.", value=True)
if use_seed:
    seed_value = st.sidebar.number_input("Random Seed", min_value=0, max_value=9999, value=42, step=1)
else:
    seed_value = None

filter_by_month = st.sidebar.checkbox("Generate for specific month only", value=False)
if filter_by_month:
    selected_month = st.sidebar.selectbox("Select Month", 
                                          ['August 2025', 'September 2025', 'October 2025', 
                                           'November 2025', 'December 2025', 'January 2026',
                                           'February 2026', 'March 2026', 'April 2026',
                                           'May 2026', 'June 2026'])
else:
    selected_month = None

st.sidebar.markdown("---")
st.sidebar.markdown("**Created for Spire School 2025-2026**")

# ==================== MAIN LOGIC ====================

if calendar_file and staff_file:
    try:
        # Load and validate data
        calendar_df = pd.read_csv(calendar_file)
        staff_df = pd.read_csv(staff_file)

        required_calendar_cols = ['date', 'day_of_week', 'needs_duty']
        missing_calendar_cols = [col for col in required_calendar_cols if col not in calendar_df.columns]
        if missing_calendar_cols:
            st.error(f"❌ Calendar CSV missing required columns: {', '.join(missing_calendar_cols)}")
            st.stop()

        if 'Unnamed: 0' in staff_df.columns:
            staff_df = staff_df.rename(columns={'Unnamed: 0': 'name'})
        
        if staff_df.columns[0] not in ['name', 'Name']:
             staff_df.columns = ['name'] + list(staff_df.columns[1:])
        elif staff_df.columns[0] == 'Name':
            staff_df = staff_df.rename(columns={'Name': 'name'})

        required_staff_cols = ['name', 'Monday', 'Tuesday', 'Wednesday']
        missing_staff_cols = [col for col in required_staff_cols if col not in staff_df.columns]
        if missing_staff_cols:
            st.error(f"❌ Staff CSV missing required columns: {', '.join(missing_staff_cols)}. Found: {', '.join(staff_df.columns)}")
            st.stop()

        try:
            calendar_df['date_parsed'] = pd.to_datetime(calendar_df['date'], format='%A, %B %d, %Y')
        except Exception as e:
            st.error(f"❌ Error parsing dates in calendar. Expected format: 'Monday, August 25, 2025'\nError: {str(e)}")
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
        
        if selected_month:
            month_mapping = {
                'August 2025': (2025, 8), 'September 2025': (2025, 9), 'October 2025': (2025, 10),
                'November 2025': (2025, 11), 'December 2025': (2025, 12), 'January 2026': (2026, 1),
                'February 2026': (2026, 2), 'March 2026': (2026, 3), 'April 2026': (2026, 4),
                'May 2026': (2026, 5), 'June 2026': (2026, 6)
            }
            year, month = month_mapping[selected_month]
            
            duty_days_to_schedule = duty_days_all[
                (duty_days_all['date_parsed'].dt.year == year) & 
                (duty_days_all['date_parsed'].dt.month == month)
            ].copy()

            period_calendar_df = all_days_for_period[
                (all_days_for_period['date_parsed'].dt.year == year) & 
                (all_days_for_period['date_parsed'].dt.month == month)
            ].copy()

            if len(duty_days_to_schedule) == 0:
                st.warning(f"⚠️ No duty days found for {selected_month}")
            
            current_period_name = selected_month
            current_year_val = year
            current_month_name = selected_month.split()[0]

        else:
            duty_days_to_schedule = duty_days_all
            period_calendar_df = all_days_for_period 
            current_period_name = "Full 2025-2026 Year"
            current_year_val = 2025 
            current_month_name = "Full Year"


        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📅 Total Duty Days to Schedule", len(duty_days_to_schedule))
        with col2:
            st.metric("👥 Total Staff", len(staff_df))
        with col3:
            avg_duties = (len(duty_days_to_schedule) * 3) / len(staff_df) if len(staff_df) > 0 else 0
            st.metric("📊 Avg Duties/Person", f"{avg_duties:.1f}")

        def generate_schedule():
            with st.spinner("Generating optimized schedule..."):
                try:
                    schedule_df, summary_df = generate_lunch_duty_schedule(duty_days_to_schedule, staff_df, seed=seed_value)
                    
                    st.session_state.schedule_df = schedule_df
                    st.session_state.summary_df = summary_df
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
            
            st.success(f"✅ Schedule for {st.session_state.period_name} ready! ({len(st.session_state.schedule_df)} duty days assigned)")

            st.subheader("📋 Duty Schedule (Assigned Days Only)")
            display_schedule = st.session_state.schedule_df.drop(columns=['date_parsed'], errors='ignore')
            # Columns are: 'date', 'day_of_week', 'main_room_1', 'main_room_2', 'quiet_room'
            display_schedule.columns = ['Date', 'Day', 'Main Room 1', 'Main Room 2', 'Quiet Room']
            st.dataframe(display_schedule, use_container_width=True, height=400)

            st.subheader("📊 Duty Distribution Summary")
            col1, col2 = st.columns(2)

            with col1:
                st.dataframe(st.session_state.summary_df, use_container_width=True, height=300)

            with col2:
                st.markdown("**Distribution Check:**")
                if not st.session_state.summary_df.empty:
                    min_duties = st.session_state.summary_df['total_duties'].min()
                    max_duties = st.session_state.summary_df['total_duties'].max()
                    diff = max_duties - min_duties

                    st.metric("Min Duties", min_duties)
                    st.metric("Max Duties", max_duties)
                    st.metric("Difference (should be ≤1)", diff, delta=None if diff <= 1 else "⚠️ Unbalanced")

                    if diff <= 1:
                        st.success("✅ Perfect balance achieved!")
                    else:
                        st.warning("⚠️ Schedule may need adjustment")
                else:
                    st.warning("No summary data to display.")

            st.subheader("💾 Download Results")

            gen_date_str = datetime.now().strftime('%m.%d.%y')
            period_str = st.session_state.period_name
            safe_period_str = period_str.replace(" ", "_").replace("-", "_")
            base_filename = f"Spire_Lunch_Duty_{safe_period_str}_gen_on_{gen_date_str}"

            export_format = st.radio("Choose export format:", 
                                    ["CSV (Data)", "PDF (Print-friendly)", "PNG (Image)"],
                                    horizontal=True)

            col1, col2 = st.columns(2)

            with col1:
                if export_format == "CSV (Data)":
                    display_schedule_csv = st.session_state.schedule_df.drop(columns=['date_parsed'], errors='ignore')
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

                else:  # PNG
                    if st.session_state.month_name == "Full Year":
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

        ### Algorithm Constraints:
        - ✅ Exactly 3 staff per duty day (2 main room, 1 quiet room)
        - ✅ Max 1 duty per person per week
        - ✅ Equal distribution (everyone gets within ±1 duties)
        - ✅ Respects individual availability constraints
        - ✅ Fair quiet room rotation

        ### Export Formats:
        - **CSV**: Raw data for *assigned duty days only*.
        - **PDF**: Professional print-ready schedule. *Shows all Mon/Tue/Wed. "NO LUNCH" days are gray.*
        - **PNG**: Image format. *Shows all Mon/Tue/Wed. "NO LUNCH" days are gray. Full-year export is a .zip.*

        ### Tips:
        - Use a random seed for reproducible results
        - **Important:** After changing the month, you *must* click "Generate Schedule" again before downloading.
        """)
