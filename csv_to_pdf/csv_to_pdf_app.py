import streamlit as st
import pandas as pd
from io import BytesIO
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from PIL import Image, ImageFont, ImageDraw

st.set_page_config(page_title="Lunch Duty PDF Generator", page_icon="🖨️", layout="wide")

# ==================== CONSTANTS ====================
class ScheduleConfig:
    """Configuration constants for schedule display"""
    PDF_COL_WIDTH = 2.5 * inch
    PDF_ROW_HEIGHT = 0.5 * inch
    MAROON = '#8B0000'
    PINK = '#FFB6D9'
    NO_LUNCH_GRAY = '#E0E0E0'

# ==================== LOGIC ====================

def generate_virtual_calendar(schedule_df):
    """
    Looks at the dates in the schedule and generates a 'virtual' calendar
    containing EVERY Mon/Tue/Wed for the months involved.
    This ensures 'No Lunch' days (gaps in the schedule) appear as gray boxes.
    """
    # 1. Figure out the time range
    min_date = schedule_df['date_parsed'].min()
    max_date = schedule_df['date_parsed'].max()
    
    # 2. Stretch to the full start/end of those months
    start_date = min_date.replace(day=1)
    end_date = max_date + pd.tseries.offsets.MonthEnd(0)
    
    # 3. Generate ALL dates in that range
    all_dates = pd.date_range(start=start_date, end=end_date)
    
    # 4. Filter for only Mon(0), Tue(1), Wed(2)
    duty_days_mask = all_dates.dayofweek.isin([0, 1, 2])
    virtual_cal = pd.DataFrame({'date_parsed': all_dates[duty_days_mask]})
    
    # 5. Add helper columns
    virtual_cal['day_of_week'] = virtual_cal['date_parsed'].dt.day_name()
    
    return virtual_cal

def prepare_schedule_dataframe(uploaded_schedule):
    """Parses the uploaded CSV and standardizes columns."""
    df = pd.read_csv(uploaded_schedule)
    
    # Normalize columns
    df.columns = [c.strip() for c in df.columns]
    
    # Map 'Nice' names back to 'Internal' names
    col_map = {
        'Date': 'date_str',
        'Main Room 1': 'main_room_1',
        'Main Room 2': 'main_room_2',
        'Quiet Room': 'quiet_room'
    }
    
    # Check for required columns
    missing = [k for k in col_map.keys() if k not in df.columns]
    if missing:
        # Fallback for internal format
        if 'date_parsed' in df.columns: return df 
        st.error(f"❌ CSV is missing columns: {', '.join(missing)}")
        return None

    df = df.rename(columns=col_map)
    
    # Smart Date Parsing (Handles "Monday, May 4, 2026" or "2026-05-04")
    df['date_parsed'] = pd.to_datetime(df['date_str'], errors='coerce')
    
    if df['date_parsed'].isnull().any():
        st.error("❌ Some dates couldn't be parsed. Ensure they look like 'Monday, May 4, 2026'.")
        return None
        
    return df

def create_pdf_schedule(schedule_df):
    """Renders the PDF using the merged data."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), topMargin=0.3*inch, bottomMargin=0.3*inch)
    elements = []
    styles = getSampleStyleSheet()

    # Styles
    main_title_style = ParagraphStyle('MainTitle', parent=styles['Heading1'], fontSize=28, textColor=colors.HexColor(ScheduleConfig.MAROON), spaceAfter=0.5*inch, alignment=1)
    month_title_style = ParagraphStyle('MonthTitle', parent=styles['Heading2'], fontSize=20, textColor=colors.HexColor(ScheduleConfig.MAROON), spaceAfter=0.3*inch, alignment=0)
    legend_style = ParagraphStyle('Legend', parent=styles['Normal'], fontSize=9, textColor=colors.grey, alignment=0)

    # 1. Generate Virtual Calendar (The "Background" Grid)
    virtual_cal = generate_virtual_calendar(schedule_df)
    
    # 2. Merge Schedule onto Virtual Calendar
    merged_df = virtual_cal.merge(
        schedule_df[['date_parsed', 'main_room_1', 'main_room_2', 'quiet_room']],
        on='date_parsed',
        how='left'
    )
    
    # 3. Add grouping info
    merged_df['week'] = merged_df['date_parsed'].dt.isocalendar().week
    merged_df['month'] = merged_df['date_parsed'].dt.month
    merged_df['year'] = merged_df['date_parsed'].dt.year

    # 4. Generate Title
    years = merged_df['year'].unique()
    year_str = f"{min(years)}" if len(years) == 1 else f"{min(years)}-{max(years)}"
    elements.append(Paragraph(f"Spire School Lunch Duty {year_str}", main_title_style))

    # 5. Build Tables by Month
    unique_months = merged_df[['year', 'month']].drop_duplicates().sort_values(['year', 'month'])
    
    for _, row in unique_months.iterrows():
        current_year = row['year']
        current_month = row['month']
        month_df = merged_df[(merged_df['year'] == current_year) & (merged_df['month'] == current_month)]
        
        if len(month_df) == 0: continue

        # Month Header
        month_name_str = month_df['date_parsed'].iloc[0].strftime('%B')
        elements.append(Paragraph(f"{month_name_str} {current_year}", month_title_style))

        # Weeks
        weeks_in_month = month_df['week'].unique()
        for week_num in sorted(weeks_in_month):
            week_data = month_df[month_df['week'] == week_num]
            if len(week_data) == 0: continue

            # Setup Grid
            table_data = [['Monday', 'Tuesday', 'Wednesday']]
            days_in_week = {}
            for day_name in ['Monday', 'Tuesday', 'Wednesday']:
                day_df = week_data[week_data['day_of_week'] == day_name]
                days_in_week[day_name] = day_df.iloc[0] if not day_df.empty else None
            
            # Header Row Dates
            header_row = []
            for d in ['Monday', 'Tuesday', 'Wednesday']:
                if days_in_week[d] is not None:
                    header_row.append(f"{d} {days_in_week[d]['date_parsed'].strftime('%b %d')}")
                else:
                    header_row.append(f"{d}")
            table_data[0] = header_row

            # Data Rows
            cell_styles = []
            for i in range(3): # Rows: Main1, Main2, Quiet
                row_list = []
                for c_idx, day in enumerate(['Monday', 'Tuesday', 'Wednesday']):
                    day_data = days_in_week[day]
                    
                    # Logic: If data exists in DF, print it. If merged row has NaN, it's a "No Lunch" day.
                    if day_data is None or pd.isna(day_data.get('main_room_1')):
                        row_list.append('NO LUNCH')
                        cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.HexColor(ScheduleConfig.NO_LUNCH_GRAY)))
                    else:
                        if i == 2: # Quiet Room
                            staff = day_data.get('quiet_room', 'UNASSIGNED')
                            cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.HexColor(ScheduleConfig.PINK)))
                        else: # Main Room
                            key = 'main_room_1' if i == 0 else 'main_room_2'
                            staff = day_data.get(key, 'UNASSIGNED')
                            cell_styles.append(('BACKGROUND', (c_idx, i+1), (c_idx, i+1), colors.white))
                        
                        if pd.isna(staff) or str(staff) == 'nan': staff = ''
                        row_list.append(staff)
                table_data.append(row_list)

            # Table Styling
            table = Table(table_data, colWidths=[ScheduleConfig.PDF_COL_WIDTH] * 3)
            table_style = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(ScheduleConfig.MAROON)),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('HEIGHT', (0, 0), (-1, -1), ScheduleConfig.PDF_ROW_HEIGHT),
            ]
            table_style.extend(cell_styles)
            table.setStyle(TableStyle(table_style))
            elements.append(table)
            elements.append(Spacer(1, 0.2*inch))
        
        # Page break after month (unless it's the last one)
        if row.name != unique_months.index[-1]:
            elements.append(PageBreak())

    # Legend
    elements.extend([
        Spacer(1, 0.2*inch),
        Paragraph("■ Pink = Quiet Lunch Room", legend_style),
        Paragraph("■ Gray = No Lunch Duty", legend_style)
    ])

    doc.build(elements)
    buffer.seek(0)
    return buffer

# ==================== MAIN UI ====================

st.title("🖨️ Spire Lunch Duty: PDF Generator")
st.markdown("""
**Utility Tool:** Upload your edited Schedule CSV.
*This tool will auto-detect the dates and rebuild the calendar grid, including gray 'No Lunch' boxes for missing days.*
""")

schedule_file = st.file_uploader("Upload Edited Schedule CSV", type=['csv'])

if schedule_file:
    # 1. Load and Prep
    schedule_df = prepare_schedule_dataframe(schedule_file)
    
    if schedule_df is not None:
        min_date = schedule_df['date_parsed'].min()
        max_date = schedule_df['date_parsed'].max()
        
        st.success(f"✅ Loaded {len(schedule_df)} duty days.")
        st.info(f"📅 Range detected: {min_date.strftime('%b %d, %Y')} to {max_date.strftime('%b %d, %Y')}")
        
        if st.button("Generate PDF", type="primary"):
            pdf_data = create_pdf_schedule(schedule_df)
            
            st.download_button(
                label="📥 Download PDF",
                data=pdf_data,
                file_name="Manual_Lunch_Schedule.pdf",
                mime="application/pdf"
            )
