from planners import Planner
from problems import HealthcareProblem, ResourceType
from simulator import Simulator
from reporter import EventLogReporter, ResourceScheduleReporter


class OptimizedPlanner(Planner):
    def __init__(self, eventlog_file, data_columns):
        super().__init__()
        self.eventlog_reporter = EventLogReporter(eventlog_file, data_columns)
        self.resource_reporter = ResourceScheduleReporter()

        # Track patient information
        self.patient_diagnoses = {}
        self.patient_first_plan = {}
        self.last_replanned = {}  # Track when cases were last replanned

    def report(self, case_id, element, timestamp, resource, lifecycle_state, data=None):
        """Minimal event reporting for efficiency"""
        self.eventlog_reporter.callback(
            case_id, element, timestamp, resource, lifecycle_state
        )
        self.resource_reporter.callback(
            case_id, element, timestamp, resource, lifecycle_state, data
        )

        if element and element.data and element.data["diagnosis"]:
            self.patient_diagnoses[case_id] = element.data["diagnosis"]

    def plan(self, cases_to_plan, cases_to_replan, simulation_time):
        """
        Priority based planning. Also takes into account replanning and day of the week.
        """
        planned_cases = []
        new_cases = []
        # Loop through cases to plan
        # print(cases_to_plan)
        for case_id in cases_to_plan:
            if case_id not in self.patient_first_plan:
                self.patient_first_plan[case_id] = simulation_time
            # --- PRIORITIZED ADMISSION PLANNING ---
            # Use minimum planning time, based on 24 hour constraint
            base_time = simulation_time + 24
            # Process new cases with priority

            # Get the diagnosis for the case
            diagnosis = self.patient_diagnoses.get(case_id, None)

            day_of_week = int((simulation_time // 24) % 7)

            for day_offset in range(14):
                target_day = (day_of_week + day_offset + 1) % 7
            # If it's a weekday, prioritize different diagnoses
            if target_day < 5:
                if diagnosis in ["B1", "B2"]:
                    priority = 1  # Higher
                elif diagnosis in ["A3", "A4", "B3", "B4"]:
                    priority = 0  # Critical
                else:
                    priority = 2  # Standard
            else:
                if diagnosis in ["B1", "B2"]:
                    priority = 0  # Critical
                elif diagnosis in ["A3", "A4", "B3", "B4"]:
                    priority = 1  # Higher
                else:
                    priority = 2  # Standard
            new_cases.append((case_id, priority))

        for i, (case_id, priority) in enumerate(new_cases):
            # Tight spacing by priority
            if priority == 0:  # Critical
                admission_time = base_time + (i * 0.25)  # 30 min spacing
            elif priority == 1:  # Higher
                admission_time = base_time + 2 + (i * 0.25)  # 30 min spacing
            else:  # Standard
                admission_time = base_time + 4 + (i * 0.25)  # 30 min spacing

            # Ensure minimum planning horizon
            admission_time = max(admission_time, simulation_time + 24)

            # Add to planned cases
            planned_cases.append((case_id, round(admission_time)))

        # --- CONTROLLED REPLANNING ---
        # Process replan cases more selectively to avoid system overload
        replan_cases = []
        for case_id in cases_to_replan:
            # Get diagnosis for replan case
            diagnosis = self.patient_diagnoses.get(case_id, None)
            wait_time = simulation_time - self.patient_first_plan.get(
                case_id, simulation_time
            )
            last_replanned = self.last_replanned.get(case_id, 0)
            time_since_replan = simulation_time - last_replanned
            # Skip cases replanned recently to avoid excessive replanning
            if time_since_replan < 24:
                continue

            # Prioritize critical or long-waiting cases
            if target_day < 5:
                if diagnosis in ["B1", "B2"] or wait_time > 30:
                    priority = 0
                elif diagnosis in ["A3", "A4", "B3", "B4"]:
                    priority = 1  # Higher
                else:
                    priority = 2
                replan_cases.append((case_id, priority, wait_time))
            else:
                if diagnosis in ["B1", "B2"] or wait_time > 30:
                    priority = 1
                elif diagnosis in ["A3", "A4", "B3", "B4"]:
                    priority = 0  # Higher
                else:
                    priority = 2
                replan_cases.append((case_id, priority, wait_time))

        # Sort by priority then wait time
        replan_cases.sort(key=lambda x: (x[1], -x[2]))

        # Limit number of replans per cycle to avoid system overload
        max_replans = min(35, len(replan_cases))
        for i in range(max_replans):
            if i < len(replan_cases):
                case_id, _, _ = replan_cases[i]
                admission_time = base_time + i  # 1 hour spacing
                planned_cases.append((case_id, round(admission_time)))
                self.last_replanned[case_id] = simulation_time

        return planned_cases

    def schedule(self, simulation_time):
        """
        High-capacity resource scheduling to minimize in-hospital waiting time
        while maintaining system performance
        """
        hour_of_day = int(simulation_time % 24)
        day_of_week = int((simulation_time // 24) % 7)

        # Only schedule at planning time (18:00)
        if hour_of_day != 18:
            return []

        schedule = []

        # Define scheduling horizons
        next_day = simulation_time + (24 - hour_of_day)
        week_cutoff = simulation_time + 158

        # Helper function to get current scheduled resources
        def get_current(time, res_type):
            default_max = {
                ResourceType.OR: 5,
                ResourceType.A_BED: 30,
                ResourceType.B_BED: 40,
                ResourceType.INTAKE: 4,
                ResourceType.ER_PRACTITIONER: 9,
            }

            return default_max.get(res_type, 0)

        # Schedule resources for 14 days (2 weeks)
        for day_offset in range(14):
            target_day = (day_of_week + day_offset + 1) % 7
            target_date = next_day + (day_offset * 24)

            # Key time blocks - just morning and afternoon for efficiency
            morning = target_date + 8  # 8 AM
            afternoon = target_date + 14  # 2 PM

            # --- HIGH-CAPACITY ALLOCATION ---

            if target_day < 5:  # Weekday
                # Weekday - high capacity for waiting time reduction

                # Morning - near maximum capacity
                morning_res = {
                    ResourceType.OR: 3,  # 60% capacity
                    ResourceType.A_BED: 25,  # 83% capacity
                    ResourceType.B_BED: 40,  # 100% capacity
                    ResourceType.INTAKE: 4,  # 100% capacity
                    ResourceType.ER_PRACTITIONER: 3,  # 33% capacity
                }

                # Afternoon - high but more balanced capacity
                afternoon_res = {
                    ResourceType.OR: 3,  # 60% capacity
                    ResourceType.A_BED: 25,  # 83% capacity
                    ResourceType.B_BED: 40,  # 100% capacity
                    ResourceType.INTAKE: 4,  # 100% capacity
                    ResourceType.ER_PRACTITIONER: 3,  # 33% capacity
                }
            else:  # Weekend
                # Weekend - moderate-high capacity

                # Morning - high capacity for backlog processing
                morning_res = {
                    ResourceType.OR: 2,  # 40% capacity
                    ResourceType.A_BED: 13,  # 43% capacity
                    ResourceType.B_BED: 40,  # 100% capacity
                    ResourceType.INTAKE: 3,  # 75% capacity
                    ResourceType.ER_PRACTITIONER: 6,  # 67% capacity
                }

                # Afternoon - moderate capacity
                afternoon_res = {
                    ResourceType.OR: 2,  # 40% capacity
                    ResourceType.A_BED: 13,  # 43% capacity
                    ResourceType.B_BED: 40,  # 100% capacity
                    ResourceType.INTAKE: 2,  # 50% capacity
                    ResourceType.ER_PRACTITIONER: 6,  # 67% capacity
                }

            # Apply morning scheduling
            for res_type, count in morning_res.items():
                if morning < week_cutoff:
                    current = get_current(morning, res_type)
                    if count > current:  # Only schedule if increasing (constraint)
                        schedule.append((res_type, morning, count))
                else:  # Beyond a week, can freely adjust
                    schedule.append((res_type, morning, count))

            # Apply afternoon scheduling
            for res_type, count in afternoon_res.items():
                if afternoon < week_cutoff:
                    current = get_current(afternoon, res_type)
                    if count > current:  # Only schedule if increasing (constraint)
                        schedule.append((res_type, afternoon, count))
                else:  # Beyond a week, can freely adjust
                    schedule.append((res_type, afternoon, count))

        return schedule


if __name__ == "__main__":
    planner = OptimizedPlanner("./temp/event_log_optimised.csv", ["diagnosis"])
    problem = HealthcareProblem()
    simulator = Simulator(planner, problem)
    result = simulator.run(365 * 24)
    print(result)
    planner.resource_reporter.create_graph(168, 168 * 4)
