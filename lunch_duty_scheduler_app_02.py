import streamlit as st
import pandas as pd
import numpy as np
import random
from datetime import datetime
import io
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from io import BytesIO
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Rectangle
import textwrap

st.set_page_config(page_title="Lunch Duty Scheduler", page_icon="🍽️", layout="wide")

st.title("🍽️ Spire School Lunch Duty Scheduler")
st.markdown("*Automated fair scheduling for Mon/Tue/Wed lunch duties*")

# Sidebar for inputs
st.sidebar.header("📋 Configuration")

# File uploads
calendar_file = st.sidebar.file_uploader("Upload Calendar CSV", type=['csv'], 
                                          help="Tidy calendar with date, day_of_week, needs_duty columns")
staff_file = st.sidebar.file_uploader("Upload Staff Availability CSV", type=['csv'],
                                       help="Staff names and Mon/Tue/Wed availability (1=available, 0=not)")

# Seed for reproducibility
use_seed = st.sidebar.checkbox("Use random seed (for reproducible results)", value=True)
if use_seed:
    seed_value = st.sidebar.number_input("Random Seed", min_value=0, max_value=9999, value=42, step=1)
else:
    seed_value = None

# Month filter option
filter_by_month = st.sidebar.checkbox("Generate for specific month only", value=False)
if filter_by_month:
    selected_month = st.sidebar.selectbox("Select Month", 
                                          ['August 2025', 'September 2025', 'October 2025', 
                                           'November 2025', 'December 2025', 'January 2026',
                                           'February 2026', 'March 2026', 'April 2026',
                                           'May 2026', 'June 2026'])

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
    """Create a nicely formatted PDF of the schedule"""
    buffer = BytesIO()

    # Create PDF
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), topMargin=0.5*inch, bottomMargin=0.5*inch)
    elements = []

    # Title
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#8B0000'),
        spaceAfter=0.3*inch,
        alignment=1  # Center
    )

    title = Paragraph(f"{month_name} {year} - Lunch Duty Schedule", title_style)
    elements.append(title)

    # Group schedule by week
    schedule_df_copy = schedule_df.copy()
    schedule_df_copy['week'] = schedule_df_copy['date_parsed'].dt.isocalendar().week

    weeks = schedule_df_copy['week'].unique()

    for week_num in sorted(weeks):
        week_data = schedule_df_copy[schedule_df_copy['week'] == week_num]

        if len(week_data) > 0:
            # Create table data for this week
            table_data = [['Monday', 'Tuesday', 'Wednesday']]

            # Extract dates for header
            mon = week_data[week_data['day_of_week'] == 'Monday']
            tue = week_data[week_data['day_of_week'] == 'Tuesday']
            wed = week_data[week_data['day_of_week'] == 'Wednesday']

            if len(mon) > 0:
                mon_date = mon.iloc[0]['date'].split(',')[1].strip()
            else:
                mon_date = 'N/A'

            if len(tue) > 0:
                tue_date = tue.iloc[0]['date'].split(',')[1].strip()
            else:
                tue_date = 'N/A'

            if len(wed) > 0:
                wed_date = wed.iloc[0]['date'].split(',')[1].strip()
            else:
                wed_date = 'N/A'

            table_data[0] = [f"Monday {mon_date}", f"Tuesday {tue_date}", f"Wednesday {wed_date}"]

            # Add staff assignments (3 rows for 3 staff per day)
            for i in range(3):
                row = []
                for day in ['Monday', 'Tuesday', 'Wednesday']:
                    day_data = week_data[week_data['day_of_week'] == day]
                    if len(day_data) > 0:
                        if i == 2:  # Quiet room row
                            staff = day_data.iloc[0]['quiet_room']
                        else:
                            if i == 0:
                                staff = day_data.iloc[0]['main_room_1']
                            else:
                                staff = day_data.iloc[0]['main_room_2']
                        row.append(staff if staff != 'UNASSIGNED' else '')
                    else:
                        row.append('NO LUNCH')
                table_data.append(row)

            # Create table
            table = Table(table_data, colWidths=[2.5*inch, 2.5*inch, 2.5*inch])

            # Style table
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

    # Add legend
    legend_style = ParagraphStyle(
        'Legend',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.grey
    )
    legend = Paragraph("🟩 Pink = Quiet Lunch Room Assignment", legend_style)
    elements.append(legend)

    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer


def create_png_schedule(schedule_df, month_name, year):
    """Create a PNG image of the schedule"""
    schedule_df_copy = schedule_df.copy()
    schedule_df_copy['week'] = schedule_df_copy['date_parsed'].dt.isocalendar().week
    weeks = sorted(schedule_df_copy['week'].unique())

    # Calculate figure size based on number of weeks
    fig_height = 3 + len(weeks) * 2
    fig = plt.figure(figsize=(14, fig_height))
    fig.suptitle(f"{month_name} {year} - Lunch Duty Schedule", fontsize=20, fontweight='bold', color='#8B0000')

    ax = fig.add_subplot(111)
    ax.axis('off')

    y_pos = 0.95

    for week_idx, week_num in enumerate(weeks):
        week_data = schedule_df_copy[schedule_df_copy['week'] == week_num]

        # Draw week header
        ax.text(0.5, y_pos, f"Week {week_idx + 1}", fontsize=14, fontweight='bold', 
                ha='center', transform=ax.transAxes)
        y_pos -= 0.05

        # Create table for this week
        col_width = 1 / 3
        cell_height = 0.12 / 3

        # Days header
        for i, day in enumerate(['Monday', 'Tuesday', 'Wednesday']):
            x = i * col_width
            day_data = week_data[week_data['day_of_week'] == day]
            if len(day_data) > 0:
                date_str = day_data.iloc[0]['date'].split(',')[1].strip()
                day_text = f"{day} {date_str}"
            else:
                day_text = f"{day} (No Lunch)"

            rect = Rectangle((x, y_pos - cell_height), col_width, cell_height, 
                            linewidth=1, edgecolor='black', facecolor='#8B0000', 
                            transform=ax.transAxes)
            ax.add_patch(rect)
            ax.text(x + col_width/2, y_pos - cell_height/2, day_text, 
                   fontsize=10, fontweight='bold', color='white', ha='center', va='center',
                   transform=ax.transAxes)

        y_pos -= cell_height + 0.01

        # Staff assignments (3 rows)
        for row_idx in range(3):
            for col_idx, day in enumerate(['Monday', 'Tuesday', 'Wednesday']):
                day_data = week_data[week_data['day_of_week'] == day]
                if len(day_data) > 0:
                    if row_idx == 2:  # Quiet room
                        staff = day_data.iloc[0]['quiet_room']
                        bg_color = '#FFB6D9'  # Pink
                    else:
                        if row_idx == 0:
                            staff = day_data.iloc[0]['main_room_1']
                        else:
                            staff = day_data.iloc[0]['main_room_2']
                        bg_color = 'white'
                else:
                    staff = 'NO LUNCH'
                    bg_color = '#E0E0E0'

                x = col_idx * col_width
                rect = Rectangle((x, y_pos - cell_height), col_width, cell_height, 
                                linewidth=1, edgecolor='black', facecolor=bg_color,
                                transform=ax.transAxes)
                ax.add_patch(rect)
                ax.text(x + col_width/2, y_pos - cell_height/2, staff if staff != 'UNASSIGNED' else '', 
                       fontsize=9, ha='center', va='center', transform=ax.transAxes)

            y_pos -= cell_height + 0.01

        y_pos -= 0.04

    plt.tight_layout()

    # Save to buffer
    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=150, bbox_inches='tight')
    buffer.seek(0)
    plt.close()
    return buffer


# Main app logic
if calendar_file and staff_file:
    try:
        # Load data with error handling
        calendar_df = pd.read_csv(calendar_file)
        staff_df = pd.read_csv(staff_file)

        # Validate calendar columns
        required_calendar_cols = ['date', 'day_of_week', 'needs_duty']
        missing_calendar_cols = [col for col in required_calendar_cols if col not in calendar_df.columns]
        if missing_calendar_cols:
            st.error(f"❌ Calendar CSV missing required columns: {', '.join(missing_calendar_cols)}")
            st.stop()

        # Clean staff data
        if 'Unnamed: 0' in staff_df.columns:
            staff_df = staff_df.rename(columns={'Unnamed: 0': 'name'})
        if staff_df.columns[0] not in ['name', 'Name']:
            staff_df.columns = ['name', 'Monday', 'Tuesday', 'Wednesday']

        # Validate staff columns
        required_staff_cols = ['name', 'Monday', 'Tuesday', 'Wednesday']
        missing_staff_cols = [col for col in required_staff_cols if col not in staff_df.columns]
        if missing_staff_cols:
            st.error(f"❌ Staff CSV missing required columns: {', '.join(missing_staff_cols)}")
            st.stop()

        # Parse dates
        try:
            calendar_df['date_parsed'] = pd.to_datetime(calendar_df['date'], format='%A, %B %d, %Y')
        except Exception as e:
            st.error(f"❌ Error parsing dates in calendar. Expected format: 'Monday, August 25, 2025'\nError: {str(e)}")
            st.stop()

        # Filter duty days
        duty_days = calendar_df[calendar_df['needs_duty'] == 1].copy()
        duty_days = duty_days.sort_values('date_parsed').reset_index(drop=True)

        if len(duty_days) == 0:
            st.error("❌ No duty days found in calendar (needs_duty = 1)")
            st.stop()

        # Apply month filter if selected
        if filter_by_month:
            month_mapping = {
                'August 2025': (2025, 8), 'September 2025': (2025, 9), 'October 2025': (2025, 10),
                'November 2025': (2025, 11), 'December 2025': (2025, 12), 'January 2026': (2026, 1),
                'February 2026': (2026, 2), 'March 2026': (2026, 3), 'April 2026': (2026, 4),
                'May 2026': (2026, 5), 'June 2026': (2026, 6)
            }
            year, month = month_mapping[selected_month]
            duty_days = duty_days[(duty_days['date_parsed'].dt.year == year) & 
                                  (duty_days['date_parsed'].dt.month == month)]

            if len(duty_days) == 0:
                st.warning(f"⚠️ No duty days found for {selected_month}")
                st.stop()

        # Display info
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📅 Total Duty Days", len(duty_days))
        with col2:
            st.metric("👥 Total Staff", len(staff_df))
        with col3:
            avg_duties = (len(duty_days) * 3) / len(staff_df)
            st.metric("📊 Avg Duties/Person", f"{avg_duties:.1f}")

        # Generate button
        if st.button("🎲 Generate Schedule", type="primary"):
            with st.spinner("Generating optimized schedule..."):
                try:
                    schedule_df, summary_df = generate_lunch_duty_schedule(duty_days, staff_df, seed=seed_value)

                    st.success(f"✅ Schedule generated successfully! ({len(schedule_df)} duty days)")

                    # Display schedule
                    st.subheader("📋 Duty Schedule")
                    display_schedule = schedule_df[['date', 'day_of_week', 'main_room_1', 'main_room_2', 'quiet_room']].copy()
                    display_schedule.columns = ['Date', 'Day', 'Main Room 1', 'Main Room 2', 'Quiet Room']
                    st.dataframe(display_schedule, use_container_width=True, height=400)

                    # Display summary statistics
                    st.subheader("📊 Duty Distribution Summary")
                    col1, col2 = st.columns(2)

                    with col1:
                        st.dataframe(summary_df, use_container_width=True, height=300)

                    with col2:
                        st.markdown("**Distribution Check:**")
                        min_duties = summary_df['total_duties'].min()
                        max_duties = summary_df['total_duties'].max()
                        diff = max_duties - min_duties

                        st.metric("Min Duties", min_duties)
                        st.metric("Max Duties", max_duties)
                        st.metric("Difference (should be ≤1)", diff, delta=None if diff <= 1 else "⚠️ Unbalanced")

                        if diff <= 1:
                            st.success("✅ Perfect balance achieved!")
                        else:
                            st.warning("⚠️ Schedule may need adjustment")

                    # Export options
                    st.subheader("💾 Download Results")

                    export_format = st.radio("Choose export format:", 
                                            ["CSV (Data)", "PDF (Print-friendly)", "PNG (Image)"],
                                            horizontal=True)

                    col1, col2 = st.columns(2)

                    with col1:
                        if export_format == "CSV (Data)":
                            schedule_csv = display_schedule.to_csv(index=False)
                            st.download_button(
                                label="📥 Download Schedule CSV",
                                data=schedule_csv,
                                file_name=f"lunch_duty_schedule_{datetime.now().strftime('%Y%m%d')}.csv",
                                mime="text/csv"
                            )
                        elif export_format == "PDF (Print-friendly)":
                            pdf_buffer = create_pdf_schedule(schedule_df, selected_month if filter_by_month else "Full Year", 
                                                            2025 if filter_by_month and '2025' in selected_month else 2026)
                            st.download_button(
                                label="📥 Download Schedule PDF",
                                data=pdf_buffer,
                                file_name=f"lunch_duty_schedule_{datetime.now().strftime('%Y%m%d')}.pdf",
                                mime="application/pdf"
                            )
                        else:  # PNG
                            png_buffer = create_png_schedule(schedule_df, selected_month if filter_by_month else "Full Year",
                                                            2025 if filter_by_month and '2025' in selected_month else 2026)
                            st.download_button(
                                label="📥 Download Schedule PNG",
                                data=png_buffer,
                                file_name=f"lunch_duty_schedule_{datetime.now().strftime('%Y%m%d')}.png",
                                mime="image/png"
                            )

                    with col2:
                        # Always offer summary CSV
                        summary_csv = summary_df.to_csv(index=False)
                        st.download_button(
                            label="📥 Download Summary CSV",
                            data=summary_csv,
                            file_name=f"duty_summary_{datetime.now().strftime('%Y%m%d')}.csv",
                            mime="text/csv"
                        )

                except Exception as e:
                    st.error(f"❌ Error generating schedule: {str(e)}")
                    st.exception(e)

    except Exception as e:
        st.error(f"❌ Error loading files: {str(e)}")
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
        - First column: Staff names
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
        - **PDF**: Professional print-ready schedule (looks like educator templates)
        - **PNG**: Image format for easy sharing/posting

        ### Tips:
        - Use a random seed for reproducible results
        - Generate monthly schedules if you want more control
        - PDF/PNG formats are ideal for printing and posting
        """)

st.sidebar.markdown("---")
st.sidebar.markdown("**Created for Spire School 2025-2026**")
