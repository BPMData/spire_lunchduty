import streamlit as st
import pandas as pd
import numpy as np
import random
from datetime import datetime
import io
from io import BytesIO
import zipfile  # <-- Added for zipping PNGs

from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
# import matplotlib.pyplot as plt # No longer needed for PIL-based PNG
# from matplotlib.patches import Rectangle # No longer needed for PIL-based PNG
from PIL import Image, ImageDraw, ImageFont


st.set_page_config(page_title="Lunch Duty Scheduler", page_icon="🍽️", layout="wide")

st.title("🍽️ Spire School Lunch Duty Scheduler")
st.markdown("*Automated fair scheduling for Mon/Tue/Wed lunch duties*")

# ==================== SESSION STATE INITIALIZATION ====================
if "schedule_df" not in st.session_state:
    st.session_state.schedule_df = None
if "summary_df" not in st.session_state:
    st.session_state.summary_df = None
if "schedule_ready" not in st.session_state:
    st.session_state.schedule_ready = False
if "month_name" not in st.session_state:
    st.session_state.month_name = "Full Year"
if "year_val" not in st.session_state:
    st.session_state.year_val = 2025 # Used for naming convention

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


def create_pdf_schedule(schedule_df, month_name, year):
    """
    Create a nicely formatted PDF of the schedule.
    Handles both single-month and multi-month (full year) requests.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), topMargin=0.5*inch, bottomMargin=0.5*inch)
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
        textColor=colors.grey
    )
    
    schedule_df_copy = schedule_df.copy()
    schedule_df_copy['week'] = schedule_df_copy['date_parsed'].dt.isocalendar().week
    schedule_df_copy['month'] = schedule_df_copy['date_parsed'].dt.month
    schedule_df_copy['year'] = schedule_df_copy['date_parsed'].dt.year

    # --- Logic for Full Year (Multi-Month) ---
    if month_name == "Full Year":
        # 1. Add Title Page
        title = Paragraph("Spire School Lunch Duty Schedule", main_title_style)
        subtitle = Paragraph("Full 2025-2026 Academic Year", month_title_style)
        elements.append(title)
        elements.append(subtitle)
        elements.append(PageBreak())

        # 2. Loop through each month
        unique_months = schedule_df_copy[['year', 'month']].drop_duplicates().sort_values(['year', 'month'])
        
        for idx, row in unique_months.iterrows():
            current_year = row['year']
            current_month = row['month']
            
            month_df = schedule_df_copy[(schedule_df_copy['year'] == current_year) & 
                                        (schedule_df_copy['month'] == current_month)]
            
            if len(month_df) == 0:
                continue

            # 3. Add Monthly Title
            month_name_str = month_df['date_parsed'].iloc[0].strftime('%B')
            month_title = Paragraph(f"{month_name_str} {current_year} - Lunch Duty Schedule", month_title_style)
            elements.append(month_title)

            # 4. Loop through weeks *within* that month
            weeks_in_month = month_df['week'].unique()
            for week_num in sorted(weeks_in_month):
                week_data = month_df[month_df['week'] == week_num]
                
                # (Existing table-building logic)
                if len(week_data) > 0:
                    table_data = [['Monday', 'Tuesday', 'Wednesday']]

                    mon = week_data[week_data['day_of_week'] == 'Monday']
                    tue = week_data[week_data['day_of_week'] == 'Tuesday']
                    wed = week_data[week_data['day_of_week'] == 'Wednesday']

                    mon_date = mon.iloc[0]['date'].split(',')[1].strip() if len(mon) > 0 else 'N/A'
                    tue_date = tue.iloc[0]['date'].split(',')[1].strip() if len(tue) > 0 else 'N/A'
                    wed_date = wed.iloc[0]['date'].split(',')[1].strip() if len(wed) > 0 else 'N/A'

                    table_data[0] = [f"Monday {mon_date}", f"Tuesday {tue_date}", f"Wednesday {wed_date}"]

                    for i in range(3):
                        row_list = []
                        for day in ['Monday', 'Tuesday', 'Wednesday']:
                            day_data = week_data[week_data['day_of_week'] == day]
                            if len(day_data) > 0:
                                if i == 2:
                                    staff = day_data.iloc[0]['quiet_room']
                                else:
                                    staff = day_data.iloc[0]['main_room_1'] if i == 0 else day_data.iloc[0]['main_room_2']
                                row_list.append(staff if staff != 'UNASSIGNED' else '')
                            else:
                                row_list.append('NO LUNCH')
                        table_data.append(row_list)

                    table = Table(table_data, colWidths=[2.5*inch, 2.5*inch, 2.5*inch])
                    table.setStyle(TableStyle([
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#8B0000')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTSIZE', (0, 0), (-1, 0), 11),
                        ('FONTSIZE', (0, 1), (-1, -1), 10),
                        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                        ('GRID', (0, 0), (-1, -1), 1, colors.black),
                        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FFB6D9')]),
                        ('HEIGHT', (0, 0), (-1, -1), 0.6*inch),
                    ]))

                    elements.append(table)
                    elements.append(Spacer(1, 0.3*inch))
            
            # 5. Add Legend and Page Break after each month
            legend = Paragraph("🟩 Pink = Quiet Lunch Room Assignment", legend_style)
            elements.append(legend)
            elements.append(PageBreak())

    # --- Logic for Single Month ---
    else:
        title_text = f"{month_name} {year} - Lunch Duty Schedule"
        title = Paragraph(title_text, main_title_style)
        elements.append(title)

        weeks = schedule_df_copy['week'].unique()

        for week_num in sorted(weeks):
            week_data = schedule_df_copy[schedule_df_copy['week'] == week_num]

            if len(week_data) > 0:
                table_data = [['Monday', 'Tuesday', 'Wednesday']]
                # (Same table-building logic as above)
                mon = week_data[week_data['day_of_week'] == 'Monday']
                tue = week_data[week_data['day_of_week'] == 'Tuesday']
                wed = week_data[week_data['day_of_week'] == 'Wednesday']
                mon_date = mon.iloc[0]['date'].split(',')[1].strip() if len(mon) > 0 else 'N/A'
                tue_date = tue.iloc[0]['date'].split(',')[1].strip() if len(tue) > 0 else 'N/A'
                wed_date = wed.iloc[0]['date'].split(',')[1].strip() if len(wed) > 0 else 'N/A'
                table_data[0] = [f"Monday {mon_date}", f"Tuesday {tue_date}", f"Wednesday {wed_date}"]
                for i in range(3):
                    row_list = []
                    for day in ['Monday', 'Tuesday', 'Wednesday']:
                        day_data = week_data[week_data['day_of_week'] == day]
                        if len(day_data) > 0:
                            if i == 2:
                                staff = day_data.iloc[0]['quiet_room']
                            else:
                                staff = day_data.iloc[0]['main_room_1'] if i == 0 else day_data.iloc[0]['main_room_2']
                            row_list.append(staff if staff != 'UNASSIGNED' else '')
                        else:
                            row_list.append('NO LUNCH')
                    table_data.append(row_list)
                
                table = Table(table_data, colWidths=[2.5*inch, 2.5*inch, 2.5*inch])
                table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#8B0000')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, 0), 11),
                    ('FONTSIZE', (0, 1), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FFB6D9')]),
                    ('HEIGHT', (0, 0), (-1, -1), 0.6*inch),
                ]))
                elements.append(table)
                elements.append(Spacer(1, 0.3*inch))
        
        legend = Paragraph("🟩 Pink = Quiet Lunch Room Assignment", legend_style)
        elements.append(legend)

    # Build the PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer


def create_single_png_schedule(schedule_df, month_name, year):
    """
    Create a clean PNG image for a SINGLE month's schedule using Pillow.
    """
    # Prepare data
    schedule_df_copy = schedule_df.copy()
    schedule_df_copy['date_parsed'] = pd.to_datetime(schedule_df_copy['date_parsed'])
    schedule_df_copy = schedule_df_copy.sort_values('date_parsed').reset_index(drop=True)
    
    # Group into weeks
    weeks = []
    current_week = []
    
    for idx, row in schedule_df_copy.iterrows():
        current_week.append(row)
        
        # If we hit Wednesday or end of data, save the week
        if row['day_of_week'] == 'Wednesday' or idx == len(schedule_df_copy) - 1:
            weeks.append(current_week)
            current_week = []
    
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
    
    # Calculate height based on number of weeks for THIS month
    img_width = cols * cell_width + 2 * padding
    img_height = title_height + (total_weeks * (rows_per_week * cell_height + week_spacing)) + padding
    
    # Create image
    img = Image.new('RGB', (img_width, img_height), 'white')
    draw = ImageDraw.Draw(img)
    
    # Load fonts (fallback to default if custom not available)
    try:
        # Using common font paths
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 32)
        header_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 16)
        cell_font = ImageFont.truetype("DejaVuSans.ttf", 14)
    except IOError:
        try:
            # Fallback for other systems
            title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
            header_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            cell_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except IOError:
            # Absolute fallback
            title_font = ImageFont.load_default()
            header_font = ImageFont.load_default()
            cell_font = ImageFont.load_default()
    
    # Colors
    maroon = '#8B0000'
    pink = '#FFB6D9'
    light_gray = '#F5F5F5'
    border = '#CCCCCC'
    
    # Draw title
    if month_name == "Full Year":
        # This function should ideally be called per-month, but handling it just in case
        title = "Full 2025-2026 Year - Lunch Duty Schedule"
    else:
        title = f"{month_name} {year} - Lunch Duty Schedule"
        
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    draw.text(((img_width - title_width) // 2, padding + 10), title, fill=maroon, font=title_font)
    
    # Starting Y position for tables
    y = title_height + padding
    
    # Draw each week
    for week_idx, week in enumerate(weeks):
        # Week header
        draw.rectangle([padding, y, img_width - padding, y + header_height], fill=maroon, outline=border, width=2)
        
        # Column headers
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
        day_names = ['Monday', 'Tuesday', 'Wednesday']
        for day_idx in range(3):
            # Get data for this day (if exists)
            row_data = None
            for day_data in week:
                if day_data['day_of_week'] == day_names[day_idx]:
                    row_data = day_data
                    break
            
            if row_data is not None:
                day_date = row_data['date_parsed'].strftime('%b %d')
                main_1 = row_data['main_room_1']
                main_2 = row_data['main_room_2']
                quiet = row_data['quiet_room']
            else:
                day_date = ''
                main_1 = main_2 = quiet = 'NO LUNCH'
            
            # Alternate row background
            row_bg = light_gray if day_idx % 2 == 0 else 'white'
            
            # Draw day cell
            draw.rectangle([padding, y, padding + cell_width, y + cell_height], 
                          fill=row_bg, outline=border, width=1)
            day_text = f"{day_names[day_idx]}\n{day_date}"
            draw.text((padding + 10, y + 10), day_text, fill='black', font=cell_font)
            
            # Draw cafeteria (combined main rooms)
            cafeteria_text = f"{main_1}\n{main_2}" if main_1 != 'NO LUNCH' else 'NO LUNCH'
            draw.rectangle([padding + cell_width, y, padding + 2*cell_width, y + cell_height],
                          fill=row_bg, outline=border, width=1)
            lines = cafeteria_text.split('\n')
            line_height_bbox = draw.textbbox((0,0), "Tg", font=cell_font)
            line_height = (line_height_bbox[3] - line_height_bbox[1]) + 4 # Add 4px spacing
            start_y = y + (cell_height - len(lines) * line_height) // 2
            for i, line in enumerate(lines):
                text_bbox = draw.textbbox((0, 0), line, font=cell_font)
                text_width = text_bbox[2] - text_bbox[0]
                draw.text((padding + cell_width + (cell_width - text_width) // 2, 
                          start_y + i * line_height), 
                         line, fill='black', font=cell_font)
            
            # Draw quiet room (pink background)
            quiet_bg = pink if quiet != 'NO LUNCH' else row_bg
            draw.rectangle([padding + 2*cell_width, y, padding + 3*cell_width, y + cell_height],
                          fill=quiet_bg, outline=border, width=1)
            text_bbox = draw.textbbox((0, 0), quiet, font=cell_font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            draw.text((padding + 2*cell_width + (cell_width - text_width) // 2,
                      y + (cell_height - text_height) // 2),
                     quiet, fill='black', font=cell_font)
            
            y += cell_height
        
        # Add spacing between weeks
        y += week_spacing
    
    # Save to buffer
    buf = BytesIO()
    img.save(buf, format='PNG', quality=95)
    buf.seek(0)
    
    return buf


def create_png_zip_schedule(schedule_df):
    """
    Creates a Zip file in memory containing one PNG per month.
    """
    zip_buffer = BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        schedule_df_copy = schedule_df.copy()
        schedule_df_copy['month'] = schedule_df_copy['date_parsed'].dt.month
        schedule_df_copy['year'] = schedule_df_copy['date_parsed'].dt.year
        
        unique_months = schedule_df_copy[['year', 'month']].drop_duplicates().sort_values(['year', 'month'])
        
        for idx, row in unique_months.iterrows():
            current_year = row['year']
            current_month = row['month']
            
            month_df = schedule_df_copy[(schedule_df_copy['year'] == current_year) & 
                                        (schedule_df_copy['month'] == current_month)]
            
            if len(month_df) == 0:
                continue

            month_name_str = month_df['date_parsed'].iloc[0].strftime('%B')
            
            # Generate the PNG for this single month
            png_buffer = create_single_png_schedule(month_df, month_name_str, current_year)
            
            # Create a clean filename for the PNG inside the zip
            png_filename = f"Spire_Lunch_Duty_{current_year}_{current_month:02d}_{month_name_str}.png"
            
            # Write the PNG data to the zip file
            zip_file.writestr(png_filename, png_buffer.getvalue())

    zip_buffer.seek(0)
    return zip_buffer


# ==================== SIDEBAR INPUT ====================
st.sidebar.header("📋 Configuration")

calendar_file = st.sidebar.file_uploader("Upload Calendar CSV", type=['csv'], 
                                          help="Tidy calendar with date, day_of_week, needs_duty columns")
staff_file = st.sidebar.file_uploader("Upload Staff Availability CSV", type=['csv'],
                                       help="Staff names and Mon/Tue/Wed availability (1=available, 0=not)")

use_seed = st.sidebar.checkbox("Use random seed (for reproducible results)", value=True)
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
        
        # Handle flexible 'name' column
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

        duty_days_all = calendar_df[calendar_df['needs_duty'] == 1].copy()
        duty_days_all = duty_days_all.sort_values('date_parsed').reset_index(drop=True)

        if len(duty_days_all) == 0:
            st.error("❌ No duty days found in calendar (needs_duty = 1)")
            st.stop()
        
        # Apply filter *if* selected, otherwise use all days
        if selected_month:
            month_mapping = {
                'August 2025': (2025, 8), 'September 2025': (2025, 9), 'October 2025': (2025, 10),
                'November 2025': (2025, 11), 'December 2025': (2025, 12), 'January 2026': (2026, 1),
                'February 2026': (2026, 2), 'March 2026': (2026, 3), 'April 2026': (2026, 4),
                'May 2026': (2026, 5), 'June 2026': (2026, 6)
            }
            year, month = month_mapping[selected_month]
            duty_days_filtered = duty_days_all[(duty_days_all['date_parsed'].dt.year == year) & 
                                               (duty_days_all['date_parsed'].dt.month == month)].copy()

            if len(duty_days_filtered) == 0:
                st.warning(f"⚠️ No duty days found for {selected_month}")
                st.stop()
            
            duty_days_to_schedule = duty_days_filtered
            current_period_name = selected_month
            current_year_val = year
            current_month_name = selected_month.split()[0]

        else:
            duty_days_to_schedule = duty_days_all
            current_period_name = "Full 2025-2026 Year"
            current_year_val = 2025 # Base year for "Full Year"
            current_month_name = "Full Year"


        # Display metrics based on the *selected* period
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📅 Total Duty Days to Schedule", len(duty_days_to_schedule))
        with col2:
            st.metric("👥 Total Staff", len(staff_df))
        with col3:
            avg_duties = (len(duty_days_to_schedule) * 3) / len(staff_df) if len(staff_df) > 0 else 0
            st.metric("📊 Avg Duties/Person", f"{avg_duties:.1f}")

        # Generate button with callback to save to session state
        def generate_schedule():
            with st.spinner("Generating optimized schedule..."):
                try:
                    schedule_df, summary_df = generate_lunch_duty_schedule(duty_days_to_schedule, staff_df, seed=seed_value)
                    
                    # Save all results to session state
                    st.session_state.schedule_df = schedule_df
                    st.session_state.summary_df = summary_df
                    st.session_state.schedule_ready = True
                    
                    # Save the *period* that was just generated for filenames and success msg
                    st.session_state.period_name = current_period_name 
                    st.session_state.month_name = current_month_name
                    st.session_state.year_val = current_year_val

                except Exception as e:
                    st.error(f"❌ Error generating schedule: {str(e)}")
                    st.exception(e) # Show full traceback in console/app

        st.button("🎲 Generate Schedule", type="primary", on_click=generate_schedule)

        # ==================== DISPLAY RESULTS (persisted in session state) ====================
        if st.session_state.schedule_ready:
            
            # --- 1. DYNAMIC SUCCESS MESSAGE (FIXED) ---
            st.success(f"✅ Schedule for {st.session_state.period_name} ready! ({len(st.session_state.schedule_df)} duty days)")

            # Display schedule table
            st.subheader("📋 Duty Schedule")
            display_schedule = st.session_state.schedule_df[['date', 'day_of_week', 'main_room_1', 'main_room_2', 'quiet_room']].copy()
            display_schedule.columns = ['Date', 'Day', 'Main Room 1', 'Main Room 2', 'Quiet Room']
            st.dataframe(display_schedule, use_container_width=True, height=400)

            # Display summary statistics
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

            # ==================== EXPORT OPTIONS ====================
            st.subheader("💾 Download Results")

            # --- FILENAME LOGIC (using persisted period name) ---
            gen_date_str = datetime.now().strftime('%m.%d.%y') # Format: 11.04.25
            
            # Use the period name we saved in session state
            period_str = st.session_state.period_name
            safe_period_str = period_str.replace(" ", "_").replace("-", "_")

            base_filename = f"Spire_Lunch_Duty_{safe_period_str}_gen_on_{gen_date_str}"
            # --- END FILENAME LOGIC ---

            export_format = st.radio("Choose export format:", 
                                    ["CSV (Data)", "PDF (Print-friendly)", "PNG (Image)"],
                                    horizontal=True)

            col1, col2 = st.columns(2)

            with col1:
                if export_format == "CSV (Data)":
                    display_schedule_csv = st.session_state.schedule_df[['date', 'day_of_week', 'main_room_1', 'main_room_2', 'quiet_room']].copy()
                    display_schedule_csv.columns = ['Date', 'Day', 'Main Room 1', 'Main Room 2', 'Quiet Room']
                    schedule_csv_data = display_schedule_csv.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Schedule CSV",
                        data=schedule_csv_data,
                        file_name=f"{base_filename}_Schedule.csv",
                        mime="text/csv"
                    )

                elif export_format == "PDF (Print-friendly)":
                    pdf_buffer = create_pdf_schedule(st.session_state.schedule_df, 
                                                    st.session_state.month_name, # "Full Year" or "September"
                                                    st.session_state.year_val)
                    st.download_button(
                        label="📥 Download Schedule PDF",
                        data=pdf_buffer,
                        file_name=f"{base_filename}_Schedule.pdf",
                        mime="application/pdf"
                    )

                else:  # PNG
                    # --- 3. PNG ZIP LOGIC (FIXED) ---
                    if st.session_state.month_name == "Full Year":
                        # Generate a ZIP of monthly PNGs
                        zip_buffer = create_png_zip_schedule(st.session_state.schedule_df)
                        st.download_button(
                            label="📥 Download All Monthly PNGs (.zip)",
                            data=zip_buffer,
                            file_name=f"{base_filename}_Monthly_Schedules.zip",
                            mime="application/zip"
                        )
                    else:
                        # Generate a single PNG for the one month
                        png_buffer = create_single_png_schedule(st.session_state.schedule_df,
                                                                st.session_state.month_name,
                                                                st.session_state.year_val)
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
        st.exception(e) # Show full traceback

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
        - **CSV**: Raw data for further analysis
        - **PDF**: Professional print-ready schedule. *Full-year export is multi-page with monthly titles.*
        - **PNG**: Image format. *Full-year export is a .zip file of monthly PNGs.*

        ### Tips:
        - Use a random seed for reproducible results
        - **Important:** After changing the month, you *must* click "Generate Schedule" again before downloading.
        """)
