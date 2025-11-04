# Spire School Lunch Duty Scheduler

Automated lunch duty scheduling app for The Spire School 2025-2026 academic year.

## Features

- 🎯 **Fair Distribution**: Everyone gets equal duties (within ±1)
- 📅 **Smart Scheduling**: Respects Mon/Tue/Wed availability
- 🔒 **Constraints**: Max 1 duty per person per week
- 🏠 **Quiet Room Rotation**: Fair distribution of quiet room assignments
- 📊 **Analytics**: View duty counts and distribution stats
- 💾 **Export**: Download schedules as CSV

## Quick Start

### Local Development
```bash
pip install -r requirements.txt
streamlit run lunch_duty_scheduler_app.py
```

### Deploy to Streamlit Cloud
1. Fork this repository
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Deploy!

## Required Files

Upload these CSV files in the app:

1. **Calendar CSV** - School calendar with duty days marked
2. **Staff Availability CSV** - Staff names and Mon/Tue/Wed availability

## Algorithm

The scheduler uses a constraint-satisfaction algorithm that:
1. Assigns exactly 3 staff per duty day
2. Ensures no one works more than once per week
3. Balances total duties across all staff
4. Respects individual availability constraints
5. Fairly rotates quiet room assignments

## Built With

- [Streamlit](https://streamlit.io) - Web framework
- [Pandas](https://pandas.pydata.org) - Data manipulation
- [NumPy](https://numpy.org) - Numerical computing

## License

MIT License - feel free to adapt for your school!
