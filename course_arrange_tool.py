import numpy as np
from collections import defaultdict, Counter
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import random
import itertools
import copy
import math
import csv
import contextlib
import io
import json

# ==============================================================================
# 核心调度器类 (增强版 - 使用非线性惩罚机制)
# ==============================================================================

class Scheduler_Final:
    """最终版调度器：使用非线性惩罚机制确保大课程的最小学生数"""
    
    def __init__(
        self,
        num_time_slots=7,
        max_iterations=15000,
        cancel_check: Optional[Callable[[], bool]] = None,
    ):  # 增加迭代次数
        self.num_time_slots = num_time_slots
        self.max_iterations = max_iterations
        self.cancel_check = cancel_check
        
        # --- 成本函数权重 (优化版) ---
        self.W_CONFLICT = 5000.0
        self.W_TIMESLOT = 0.3
        self.W_SECTION = 10.0
        self.W_EXCESS_VARIANCE = 300
        self.W_MIN_STUDENTS = 1000.0  # 降低权重，因为使用平方惩罚
        
        # --- 最小学生数约束参数 ---
        self.MIN_STUDENTS_THRESHOLD = 25
        self.MAX_STUDENTS_THRESHOLD = 65  # 课程总人数超过这个数才应用最小人数约束
        self.MIN_STUDENTS_PER_SECTION = 12  # 每个分班的最小学生数
        self.MAX_STUDENTS_PER_SECTION = 30 

        # --- 数据存储 ---
        self.student_courses = {}
        self.course_sections_map = {}
        self.section_to_course = {}
        self.all_sections = []
        self.memo_student_assignment = {}
        self.memo_schedule_cost = {}
        self.course_student_count = {}  # 存储每门课程的学生总数
        self.block_ban_map = {
            "AP Calculus BC": {4,1},
            "AP Statistics": {4,1},  
            "Maths 12: Spatial analytic geometry": {4,1}, 
            "Maths 12: Math modeling": {4,1}, 
            "DP Math HL G12": {4,1}, 
            "Accelerated DP Math HL 11": {4,1},  # <-- 【关键】暂时禁用这条规则
            "AP Art and Design":{0,3,6,},
            "AP Computer Science A":{4,0,2},
            "AP Computer Science Principles":{4,0,2}
        }
        self.forbidden_course_groups = {
    ("Accelerated Economics", "Interdisciplinary Studies(IDS)"),
    ("Interdisciplinary Studies(IDS)", "AP Seminar","AP Seminar AC"),
    ("DP English A: Literature G12", "Further English: Literature Philosophy & the Meaning of Life"),
    ("DP English A: Literature G11", "Further English: Literature Philosophy & the Meaning of Life"),}

    def _clean_and_prepare_data(
        self,
        student_data: Dict,
        custom_sections: Dict,
        course_name_map: Optional[Dict[str, str]] = None,
    ):
        print("="*60)
        print(f"📚 加载数据 (硬性约束: {self.num_time_slots}个时间段)")
        print("="*60)
        
        default_course_name_map = {
            "DP English A: Literature": "DP English A – Literature",
            "AP English Languagae and Composition": "AP English Language and Composition",
            "Accelerated DP Math HL": "Accelerated DP Math HL 11"  # 添加这个映射
        }
        remap = course_name_map if course_name_map is not None else default_course_name_map
        
        # 清理学生数据，去除空字符串课程
        cleaned_student_data = {}
        for sid, courses in student_data.items():
            # 过滤掉空字符串
            cleaned_courses = [c for c in courses if c and c.strip()]
            cleaned_courses = sorted(list(set([remap.get(c, c) for c in cleaned_courses])))
            if cleaned_courses:  # 只保留有有效课程的学生
                cleaned_student_data[sid] = cleaned_courses
        
        self.student_courses = cleaned_student_data
        
        # 统计每门课程的学生总数
        self.course_student_count = Counter()
        for sid, courses in self.student_courses.items():
            for course in courses:
                self.course_student_count[course] += 1
        
        cleaned_sections = {}
        for course, count in custom_sections.items():
            canonical_name = remap.get(course, course)
            cleaned_sections[canonical_name] = max(cleaned_sections.get(canonical_name, 0), count)

        for course, num_sections in cleaned_sections.items():
            sections = [f"{course}_{i+1}" for i in range(num_sections)]
            self.course_sections_map[course] = sections
            for section in sections:
                self.section_to_course[section] = course
                self.all_sections.append(section)
        
        print(f"✅ 学生数: {len(self.student_courses)}")
        print(f"✅ 清理后课程数: {len(cleaned_sections)}")
        print(f"✅ 总分课数: {len(self.all_sections)}")
        
        # 显示需要应用最小学生数约束的课程
        print(f"\n📊 选课人数超过{self.MIN_STUDENTS_THRESHOLD}人的课程 (需确保每个分班至少{self.MIN_STUDENTS_PER_SECTION}人):")
        large_courses = [(course, count) for course, count in self.course_student_count.items() 
                        if count > self.MIN_STUDENTS_THRESHOLD and len(self.course_sections_map.get(course, [])) > 1]
        for course, count in sorted(large_courses, key=lambda x: x[1], reverse=True):
            sections = self.course_sections_map.get(course, [])
            print(f"   {course:<50} | 总人数: {count} | 分班数: {len(sections)}")
        
        # 特别提示已放开的约束
        # print("\n📌 注意：已暂时放开 Accelerated DP Math HL 11 的时间段限制")
        print("📌 优化策略：使用非线性惩罚机制，对严重偏离的情况给予更大惩罚")

    def diagnose_conflicts(self, schedule: Dict[str, int]):
        """诊断冲突的具体原因"""
        print("\n" + "="*60)
        print("🔍 冲突诊断分析")
        print("="*60)
        
        conflicted_students = []
        course_conflict_stats = Counter()
        course_pair_conflicts = Counter()
        
        for student_id, courses in self.student_courses.items():
            assignment = self._get_single_student_assignment(student_id, schedule)
            if assignment is None:
                conflicted_students.append(student_id)
                # 统计这个学生选的课程
                for course in courses:
                    course_conflict_stats[course] += 1
                # 统计课程对
                for i in range(len(courses)):
                    for j in range(i+1, len(courses)):
                        pair = tuple(sorted([courses[i], courses[j]]))
                        course_pair_conflicts[pair] += 1
        
        print(f"\n📊 冲突学生数: {len(conflicted_students)}/{len(self.student_courses)}")
        
        print("\n🔸 最容易造成冲突的课程 (选这门课的冲突学生数):")
        for course, count in course_conflict_stats.most_common(15):
            total_students = sum(1 for sid, courses in self.student_courses.items() if course in courses)
            print(f"   {course:<50} | 冲突学生: {count}/{total_students} ({count/total_students*100:.1f}%)")
        
        print("\n🔸 最容易冲突的课程组合 (同时选这两门课的冲突学生数):")
        for (course1, course2), count in course_pair_conflicts.most_common(15):
            total_students = sum(1 for sid, courses in self.student_courses.items() 
                               if course1 in courses and course2 in courses)
            print(f"   {course1[:30]:<30} + {course2[:30]:<30} | 冲突: {count}/{total_students}")
        
        # 分析被限制课程的时间段使用情况
        print("\n🔸 受限课程的时间段分配:")
        for course, banned_blocks in self.block_ban_map.items():
            sections = self.course_sections_map.get(course, [])
            if sections:
                slots = [schedule.get(sec, -1) for sec in sections]
                print(f"   {course:<40} | 禁止: {sorted(banned_blocks)} | 实际: {slots}")
        
        # 特别显示 Accelerated DP Math HL 11 的情况
        if "Accelerated DP Math HL 11" in self.course_sections_map:
            sections = self.course_sections_map["Accelerated DP Math HL 11"]
            slots = [schedule.get(sec, -1) for sec in sections]
            print(f"   {'Accelerated DP Math HL 11':<40} | 禁止: [已放开] | 实际: {slots}")

    def optimize_schedule(self) -> Dict[str, int]:
        print("\n⚡ 开始优化 (最终均衡版 + 非线性惩罚机制)...")

        if self.cancel_check and self.cancel_check():
            raise RuntimeError("Scheduling was cancelled.")
        
        best_schedule = self._initial_schedule()
        best_cost, _ = self._evaluate_schedule_with_balance(best_schedule)
        
        print("\n初始方案评估中...")
        self.print_evaluation_summary("初始方案", best_cost, best_schedule)
        
        current_schedule = copy.deepcopy(best_schedule)
        temperature = 200.0
        no_improvement_count = 0
        
        for iteration in range(self.max_iterations):
            if self.cancel_check and self.cancel_check():
                raise RuntimeError("Scheduling was cancelled.")
            neighbor = self._generate_neighbor(current_schedule)
            
            schedule_tuple = tuple(sorted(neighbor.items()))
            if schedule_tuple not in self.memo_schedule_cost:
                self._evaluate_schedule_with_balance(neighbor)
            
            current_cost, _ = self.memo_schedule_cost[tuple(sorted(current_schedule.items()))]
            neighbor_cost, _ = self.memo_schedule_cost[schedule_tuple]
            
            if neighbor_cost < current_cost:
                current_schedule = neighbor
                if neighbor_cost < best_cost:
                    best_schedule = copy.deepcopy(neighbor)
                    best_cost = neighbor_cost
                    no_improvement_count = 0
            else:
                prob = np.exp(-(neighbor_cost - current_cost) / temperature)
                if random.random() < prob:
                    current_schedule = neighbor
                no_improvement_count += 1
            
            temperature *= 0.9995  # 更缓慢的降温
            
            if (iteration + 1) % 1000 == 0:
                self.print_evaluation_summary(f"迭代 {iteration+1}", best_cost, best_schedule)
            
            if no_improvement_count > 2000:  # 增加容忍度
                print(f"\n⚠️ 连续{no_improvement_count}次无改进，提前结束")
                break
        
        return best_schedule

    def _evaluate_schedule_with_balance(self, schedule: Dict[str, int]) -> Tuple[float, Dict]:
        schedule_tuple = tuple(sorted(schedule.items()))
        if schedule_tuple in self.memo_schedule_cost:
            return self.memo_schedule_cost[schedule_tuple]

        conflict_count = self._count_conflicts(schedule)
        conflict_cost = conflict_count * self.W_CONFLICT

        slot_loads = Counter(schedule.values())
        slot_load_values = [slot_loads.get(i, 0) for i in range(self.num_time_slots)]
        timeslot_cost = np.var(slot_load_values) * self.W_TIMESLOT
        
        simulated_loads = self._get_simulated_student_loads(schedule)
        total_section_variance = 0
        excess_variance_penalty = 0
        min_students_penalty = 0  # 最小学生数惩罚
        
        for course, sections in self.course_sections_map.items():
            if len(sections) > 1:
                section_loads = [simulated_loads.get(sec, 0) for sec in sections]
                variance = np.var(section_loads)
                total_section_variance += variance
                
                # 超额方差惩罚
                if variance > 80:
                    excess_variance_penalty += (variance - 80) * self.W_EXCESS_VARIANCE
                
                # 非线性最小学生数约束惩罚
                course_total_students = self.course_student_count.get(course, 0)
                if course_total_students > self.MIN_STUDENTS_THRESHOLD:
                    for load in section_loads:
                        if load < self.MIN_STUDENTS_PER_SECTION:
                            deficit = self.MIN_STUDENTS_PER_SECTION - load
                            # 对缺口人数进行平方惩罚，让严重不足的情况代价剧增
                            # 比如缺1人罚1分，缺8人罚64分
                            penalty_score = deficit * deficit #* deficit
                            min_students_penalty += penalty_score * self.W_MIN_STUDENTS
                if course_total_students < self.MAX_STUDENTS_THRESHOLD:
                    for load in section_loads:
                        if load > self.MAX_STUDENTS_PER_SECTION:
                            deficit = load - self.MAX_STUDENTS_PER_SECTION 
                            # 对缺口人数进行平方惩罚，让严重不足的情况代价剧增
                            # 比如缺1人罚1分，缺8人罚64分
                            penalty_score = deficit * deficit * deficit
                            min_students_penalty += penalty_score * self.W_MIN_STUDENTS


        section_cost = total_section_variance * self.W_SECTION
        
        total_cost = conflict_cost + timeslot_cost + section_cost + excess_variance_penalty + min_students_penalty
        details = {
            'conflict_count': conflict_count,
            'timeslot_variance': np.var(slot_load_values),
            'section_variance': total_section_variance,
            'min_students_violations': min_students_penalty / self.W_MIN_STUDENTS if self.W_MIN_STUDENTS > 0 else 0
        }
        
        self.memo_schedule_cost[schedule_tuple] = (total_cost, details)
        return total_cost, details

    def _get_simulated_student_loads(self, schedule: Dict[str, int]) -> Counter:
        section_loads = Counter()
        self.memo_student_assignment.clear()
        
        for student_id in self.student_courses:
            assignment = self._get_single_student_assignment(student_id, schedule)
            if assignment:
                for section in assignment:
                    section_loads[section] += 1
        return section_loads

    def _count_conflicts(self, schedule: Dict[str, int]) -> int:
        conflicted_students = 0
        self.memo_student_assignment.clear()
        for student_id in self.student_courses:
            if self._get_single_student_assignment(student_id, schedule) is None:
                conflicted_students += 1
        return conflicted_students

    def _get_single_student_assignment(self, student_id: int, schedule: Dict[str, int]) -> List[str] or None:
        state_key = (student_id, tuple(sorted(schedule.items())))
        if state_key in self.memo_student_assignment:
            return self.memo_student_assignment[state_key]

        student_courses = self.student_courses.get(student_id, [])
        if not student_courses: return []

        sorted_courses = sorted(student_courses, key=lambda c: len(self.course_sections_map.get(c, [])), reverse=False)
        
        path = self._find_path_backtracking([], 0, set(), sorted_courses, schedule)
        self.memo_student_assignment[state_key] = path
        return path

    def _find_path_backtracking(self, current_path: List, course_idx: int, used_slots: Set[int], courses: List[str], schedule: Dict[str, int]) -> List[str] or None:
        if course_idx == len(courses): return current_path
        current_course = courses[course_idx]
        sections = self.course_sections_map.get(current_course, [])
        
        for section in sections:
            if section in schedule:
                slot = schedule[section]
                if slot not in used_slots:
                    used_slots.add(slot)
                    current_path.append(section)
                    result = self._find_path_backtracking(current_path, course_idx + 1, used_slots, courses, schedule)
                    if result: return result
                    current_path.pop()
                    used_slots.remove(slot)
        return None

    def _initial_schedule(self) -> Dict[str, int]:
        schedule = {}
        for section in self.all_sections:
            course = self.section_to_course[section]
            banned_blocks = self.block_ban_map.get(course, set())
            
            # 选出允许的 block 再随机
            allowed_blocks = [i for i in range(self.num_time_slots) if i not in banned_blocks]
            if not allowed_blocks:
                # 如果没有约束，所有时间段都可用
                allowed_blocks = list(range(self.num_time_slots))
            schedule[section] = random.choice(allowed_blocks)
        
        return schedule

    def _generate_neighbor(self, schedule: Dict[str, int]) -> Dict[str, int]:
        neighbor = copy.deepcopy(schedule)
        
        # 增加针对性的邻域操作
        if random.random() < 0.2:  # 20%的概率进行针对性优化
            # 找出人数过少的分班，尝试改善
            simulated_loads = self._get_simulated_student_loads(schedule)
            problem_sections = []
            
            for course, sections in self.course_sections_map.items():
                course_total = self.course_student_count.get(course, 0)
                if course_total > self.MIN_STUDENTS_THRESHOLD and len(sections) > 1:
                    for sec in sections:
                        load = simulated_loads.get(sec, 0)
                        if load < self.MIN_STUDENTS_PER_SECTION:
                            deficit = self.MIN_STUDENTS_PER_SECTION - load
                            problem_sections.append((sec, deficit))
            
            if problem_sections:
                # 选择缺口最大的分班进行优化
                problem_sections.sort(key=lambda x: x[1], reverse=True)
                section_to_optimize = problem_sections[0][0]
                course = self.section_to_course[section_to_optimize]
                
                # 尝试将这个分班移到不同的时间段
                banned_blocks = self.block_ban_map.get(course, set())
                current_slot = schedule[section_to_optimize]
                other_sections = [s for s in self.course_sections_map[course] if s != section_to_optimize]
                used_slots = {schedule[s] for s in other_sections}
                
                allowed_blocks = [i for i in range(self.num_time_slots) 
                                if i not in banned_blocks and i not in used_slots and i != current_slot]
                
                if allowed_blocks:
                    neighbor[section_to_optimize] = random.choice(allowed_blocks)
        
        elif random.random() < 0.7:
            # 移动一个 section 到允许的 block
            section_to_move = random.choice(self.all_sections)
            course = self.section_to_course[section_to_move]
            banned_blocks = self.block_ban_map.get(course, set())
            allowed_blocks = [i for i in range(self.num_time_slots) if i not in banned_blocks]
            
            if not allowed_blocks:
                allowed_blocks = list(range(self.num_time_slots))
            
            new_block = random.choice(allowed_blocks)
            neighbor[section_to_move] = new_block
        else:
            # 交换两个 section 的 block，只允许合法交换
            if len(self.all_sections) < 2:
                return neighbor
            s1, s2 = random.sample(self.all_sections, 2)
            c1, c2 = self.section_to_course[s1], self.section_to_course[s2]
            b1, b2 = schedule[s1], schedule[s2]
            
            # 检查交换后是否合法
            banned1 = self.block_ban_map.get(c1, set())
            banned2 = self.block_ban_map.get(c2, set())
            
            if b2 not in banned1 and b1 not in banned2:
                neighbor[s1], neighbor[s2] = b2, b1

        return self._fix_hard_constraints(neighbor)

    def _fix_hard_constraints(self, schedule: Dict[str, int]) -> Dict[str, int]:
        """修复硬性约束 - 确保同一课程的不同分班不在同一时段，同时考虑block_ban_map"""
        for course, sections in self.course_sections_map.items():
            if len(sections) > 1:
                slot_usage = defaultdict(list)
                for sec in sections:
                    slot_usage[schedule[sec]].append(sec)
                    
                for slot, used_secs in slot_usage.items():
                    if len(used_secs) > 1:
                        # 需要移动除第一个外的其他分班
                        banned_blocks = self.block_ban_map.get(course, set())
                        
                        for sec_to_move in used_secs[1:]:
                            current_course_slots = {schedule[s] for s in sections if s != sec_to_move}
                            # 找到既不冲突又不被禁止的时间段
                            available_slots = [s for s in range(self.num_time_slots) 
                                             if s not in current_course_slots and s not in banned_blocks]
                            
                            if available_slots:
                                schedule[sec_to_move] = random.choice(available_slots)
                            else:
                                # 如果没有完全满足的时间段，至少避免同课程冲突
                                # 优先选择未被禁止的时间段
                                non_conflict_slots = [s for s in range(self.num_time_slots) 
                                                    if s not in current_course_slots]
                                if non_conflict_slots:
                                    # 优先选择非禁止的时间段
                                    non_banned_slots = [s for s in non_conflict_slots if s not in banned_blocks]
                                    if non_banned_slots:
                                        schedule[sec_to_move] = random.choice(non_banned_slots)
                                    else:
                                        # 实在没办法，选择一个冲突最少的
                                        schedule[sec_to_move] = random.choice(non_conflict_slots)
        for group in getattr(self, "forbidden_course_groups", []):
            group_courses = list(group)
            for i in range(len(group_courses)):
                for j in range(i + 1, len(group_courses)):
                    course1, course2 = group_courses[i], group_courses[j]
                    secs1 = self.course_sections_map.get(course1, [])
                    secs2 = self.course_sections_map.get(course2, [])
                    for sec1 in secs1:
                        for sec2 in secs2:
                            if schedule.get(sec1) == schedule.get(sec2):
                                current_slot = schedule[sec1]
                                banned = self.block_ban_map.get(course2, set())
                                current_course_slots = {schedule[s] for s in secs2 if s != sec2}
                                available_slots = [
                                    s for s in range(self.num_time_slots)
                                    if s != current_slot and s not in current_course_slots and s not in banned
                                ]
                                if available_slots:
                                    schedule[sec2] = random.choice(available_slots)
                                else:
                                    non_conflict_slots = [
                                        s for s in range(self.num_time_slots)
                                        if s != current_slot and s not in current_course_slots
                                    ]
                                    if non_conflict_slots:
                                        schedule[sec2] = random.choice(non_conflict_slots)
        return schedule
    
    def print_evaluation_summary(self, title: str, cost: float, schedule: Dict[str, int]):
        _, details = self._evaluate_schedule_with_balance(schedule)
        satisfaction_rate = (len(self.student_courses) - details['conflict_count']) / len(self.student_courses) * 100
        print(f"--- {title} ---")
        print(f"  满足率: {satisfaction_rate:.2f}% (冲突: {details['conflict_count']})")
        print(f"  均衡度 (时段/分课方差): {details['timeslot_variance']:.2f} / {details['section_variance']:.2f}")
        print(f"  最小学生数违反得分: {details.get('min_students_violations', 0):.0f}")
        print(f"  综合成本: {cost:.2f}")

    def show_final_report(self, schedule: Dict[str, int]):
        print("\n" + "="*80)
        print(f"📊 最终结果分析 ({self.num_time_slots}个时间段)")
        print("="*80)
        
        _, details = self._evaluate_schedule_with_balance(schedule)
        satisfaction_rate = (len(self.student_courses) - details['conflict_count']) / len(self.student_courses) * 100
        print(f"✅ 学生满足率: {satisfaction_rate:.2f}%")
        print(f"   - 无冲突学生: {len(self.student_courses) - details['conflict_count']}/{len(self.student_courses)}")

        print("\n" + "-"*40)
        print("⚖️ 负载均衡分析")
        print("-" * 40)
        
        slot_loads = Counter(schedule.values())
        slot_load_values = [slot_loads.get(i, 0) for i in range(self.num_time_slots)]
        print("  📅 时间段负载 (每个时间段的课程数):")
        for i, count in enumerate(slot_load_values):
            print(f"     时间段 {i}: {count} 门课")
        print(f"     -> 均衡度 (方差): {details['timeslot_variance']:.2f} (越小越好)")

        simulated_loads = self._get_simulated_student_loads(schedule)
        print("\n  👥 分课人数负载 (预估每个分课的学生数):")
        multi_section_courses = {c: s for c, s in self.course_sections_map.items() if len(s) > 1}
        
        # 分别显示大课程和普通课程
        large_courses = []
        normal_courses = []
        
        for course, sections in sorted(list(multi_section_courses.items())):
            loads = [simulated_loads.get(sec, 0) for sec in sections]
            variance = np.var(loads)
            course_total = self.course_student_count.get(course, 0)
            
            if course_total > self.MIN_STUDENTS_THRESHOLD:
                large_courses.append((course, sections, loads, variance, course_total))
            else:
                normal_courses.append((course, sections, loads, variance, course_total))
        
        # 显示需要满足最小学生数的大课程
        if large_courses:
            print(f"\n  📍 选课人数>{self.MIN_STUDENTS_THRESHOLD}的课程 (每个分班需≥{self.MIN_STUDENTS_PER_SECTION}人):")
            violation_count = 0
            for course, sections, loads, variance, total in large_courses:
                min_load = min(loads)
                if min_load < self.MIN_STUDENTS_PER_SECTION:
                    status = "❌"
                    deficit = self.MIN_STUDENTS_PER_SECTION - min_load
                    note = f" [最少{min_load}人，缺{deficit}人!]"
                    violation_count += 1
                else:
                    status = "✅"
                    note = ""
                print(f"     {status} {course[:40]:<40} | 总:{total} | 人数:{str(loads):<20} | 方差:{variance:.2f}{note}")
            
            print(f"\n  📊 大课程达标率: {(len(large_courses)-violation_count)/len(large_courses)*100:.1f}% ({len(large_courses)-violation_count}/{len(large_courses)})")
        
        # 显示普通课程
        if normal_courses:
            print(f"\n  📍 普通课程 (选课人数≤{self.MIN_STUDENTS_THRESHOLD}):")
            for course, sections, loads, variance, total in normal_courses:
                status = "✅" if variance < 100 else "⚠️"
                print(f"     {status} {course[:40]:<40} | 总:{total} | 人数:{str(loads):<20} | 方差:{variance:.2f}")
        
        print("\n" + "="*80)
        print("📋 最终完整课程表")
        print("="*80)
        timetable_by_slot = defaultdict(list)
        for section, slot in schedule.items():
            timetable_by_slot[slot].append(section)
            
        for slot_id in range(self.num_time_slots):
            sections_in_slot = timetable_by_slot.get(slot_id, [])
            sorted_sections = sorted(sections_in_slot, key=lambda s: self.section_to_course.get(s, s))
            print(f"\n--- 🕒 时间段 {slot_id} (共 {len(sorted_sections)} 个分课) ---")
            for sec in sorted_sections:
                 print(f"    - {sec}")
    def export_partition_matrix_csv(self, schedule: Dict[str, int], filename: str = "partition_matrix.csv"):
        """导出课程 × 时间段的人数分布为 CSV 文件"""
        print(f"📁 正在导出 partition matrix 到 {filename} ...")
        
        # 模拟每个分课的学生数
        section_loads = self._get_simulated_student_loads(schedule)
        
        # 初始化：课程 -> [slot0人数, slot1人数, ..., slotN人数]
        course_slot_matrix = defaultdict(lambda: [0] * self.num_time_slots)
        
        for section, slot in schedule.items():
            course = self.section_to_course.get(section, section)
            count = section_loads.get(section, 0)
            course_slot_matrix[course][slot] += count

        # 将课程按字典序排序（可改为其他规则）
        sorted_courses = sorted(course_slot_matrix.keys())

        with open(filename, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            # 写标题
            header = ["Course"] + [str(i) for i in range(self.num_time_slots)]
            writer.writerow(header)
            
            # 写数据行
            for course in sorted_courses:
                row = [course] + course_slot_matrix[course]
                writer.writerow(row)

        print("✅ 导出完成！")

# ==============================================================================
# 3. 主运行函数
# ==============================================================================

TOOL_PARAMETERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "student_courses": {
            "type": "object",
            "description": (
                "Student course selections. Keys are student IDs, values are arrays of course names. "
                "Example: {'1001': ['ELA 11', 'AP Calculus BC']}."
            ),
            "additionalProperties": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "section_counts": {
            "type": "object",
            "description": (
                "Number of sections to create for each course. Keys are course names, values are positive integers."
            ),
            "additionalProperties": {
                "type": "integer",
                "minimum": 1,
            },
        },
        "num_time_slots": {
            "type": "integer",
            "description": "Number of timetable blocks/time slots to arrange sections into.",
            "minimum": 2,
            "maximum": 12,
            "default": 7,
        },
        "block_ban_map": {
            "type": "object",
            "description": (
                "Optional hard constraints forbidding courses from time slots. "
                "Keys are course names, values are arrays of zero-based time slot indexes."
            ),
            "additionalProperties": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
            },
            "default": {},
        },
        "forbidden_course_groups": {
            "type": "array",
            "description": (
                "Optional course groups that should not share the same time slot. "
                "Each group is an array of two or more course names."
            ),
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
            },
            "default": [],
        },
        "course_name_map": {
            "type": "object",
            "description": (
                "Optional course-name normalization map. Keys are incoming names, values are canonical names."
            ),
            "additionalProperties": {"type": "string"},
            "default": {},
        },
        "min_students_threshold": {
            "type": "integer",
            "description": "Apply minimum-section-size penalties only to courses with more students than this.",
            "minimum": 0,
            "default": 25,
        },
        "max_students_threshold": {
            "type": "integer",
            "description": "Apply maximum-section-size penalties only to courses with fewer students than this.",
            "minimum": 0,
            "default": 65,
        },
        "min_students_per_section": {
            "type": "integer",
            "description": "Preferred minimum estimated students per section for large courses.",
            "minimum": 0,
            "default": 12,
        },
        "max_students_per_section": {
            "type": "integer",
            "description": "Preferred maximum estimated students per section.",
            "minimum": 1,
            "default": 30,
        },
        "cost_weights": {
            "type": "object",
            "description": "Optional optimizer weights. Omitted fields use scheduler defaults.",
            "properties": {
                "conflict": {"type": "number", "minimum": 0, "default": 5000.0},
                "timeslot": {"type": "number", "minimum": 0, "default": 0.3},
                "section": {"type": "number", "minimum": 0, "default": 10.0},
                "excess_variance": {"type": "number", "minimum": 0, "default": 300.0},
                "min_students": {"type": "number", "minimum": 0, "default": 1000.0},
            },
            "additionalProperties": False,
            "default": {},
        },
        "max_iterations": {
            "type": "integer",
            "description": "Maximum simulated-annealing iterations. Higher values may improve quality but take longer.",
            "minimum": 100,
            "maximum": 100000,
            "default": 20000,
        },
        "seed": {
            "type": ["integer", "null"],
            "description": "Optional random seed for reproducible schedules.",
            "default": None,
        },
        "export_csv_path": {
            "type": ["string", "null"],
            "description": "Optional path for exporting the course-by-time-slot partition matrix CSV.",
            "default": None,
        },
        "include_schedule": {
            "type": "boolean",
            "description": "Whether to include the final schedule grouped by time slot.",
            "default": True,
        },
        "include_section_loads": {
            "type": "boolean",
            "description": "Whether to include estimated student loads for each section.",
            "default": True,
        },
        "include_diagnostics": {
            "type": "boolean",
            "description": "Whether to include the text conflict-diagnostics report.",
            "default": False,
        },
        "verbose": {
            "type": "boolean",
            "description": "Whether to allow optimizer progress logs to print to stdout.",
            "default": False,
        },
    },
    "required": ["student_courses", "section_counts"],
    "additionalProperties": False,
}

COURSE_ARRANGE_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "arrange_courses",
        "description": (
            "Create an optimized school course timetable from caller-provided student course selections, "
            "section counts, time slots, and scheduling constraints."
        ),
        "parameters": TOOL_PARAMETERS_SCHEMA,
    },
}

RESPONSES_COURSE_ARRANGE_TOOL: Dict[str, Any] = {
    "type": "function",
    "name": "arrange_courses",
    "description": COURSE_ARRANGE_TOOL["function"]["description"],
    "parameters": TOOL_PARAMETERS_SCHEMA,
}

TOOLS: List[Dict[str, Any]] = [COURSE_ARRANGE_TOOL]
RESPONSES_TOOLS: List[Dict[str, Any]] = [RESPONSES_COURSE_ARRANGE_TOOL]


def _schedule_by_slot(scheduler: Scheduler_Final, schedule: Dict[str, int]) -> Dict[str, List[str]]:
    timetable_by_slot = defaultdict(list)
    for section, slot in schedule.items():
        timetable_by_slot[slot].append(section)

    return {
        str(slot): sorted(
            timetable_by_slot.get(slot, []),
            key=lambda section: scheduler.section_to_course.get(section, section),
        )
        for slot in range(scheduler.num_time_slots)
    }


def _course_slot_matrix(scheduler: Scheduler_Final, schedule: Dict[str, int]) -> Dict[str, List[int]]:
    section_loads = scheduler._get_simulated_student_loads(schedule)
    matrix = defaultdict(lambda: [0] * scheduler.num_time_slots)

    for section, slot in schedule.items():
        course = scheduler.section_to_course.get(section, section)
        matrix[course][slot] += int(section_loads.get(section, 0))

    return {course: matrix[course] for course in sorted(matrix)}


def _normalize_student_courses(student_courses: Dict[Any, List[str]]) -> Dict[int, List[str]]:
    normalized: Dict[int, List[str]] = {}

    for generated_id, (raw_student_id, courses) in enumerate(student_courses.items(), start=1):
        if not isinstance(courses, list):
            raise ValueError(f"Courses for student {raw_student_id!r} must be a list.")

        clean_courses = [course for course in courses if isinstance(course, str) and course.strip()]
        if clean_courses:
            normalized[generated_id] = clean_courses

    if not normalized:
        raise ValueError("student_courses must contain at least one student with at least one course.")

    return normalized


def _normalize_section_counts(section_counts: Dict[str, int]) -> Dict[str, int]:
    normalized = {}
    for course, count in section_counts.items():
        if not isinstance(course, str) or not course.strip():
            raise ValueError("section_counts keys must be non-empty course names.")
        if int(count) < 1:
            raise ValueError(f"Section count for {course!r} must be at least 1.")
        normalized[course] = int(count)

    if not normalized:
        raise ValueError("section_counts must contain at least one course.")

    return normalized


def _validate_course_coverage(
    student_courses: Dict[int, List[str]],
    section_counts: Dict[str, int],
    course_name_map: Dict[str, str],
) -> None:
    configured_courses = {course_name_map.get(course, course) for course in section_counts}
    selected_courses = {
        course_name_map.get(course, course)
        for courses in student_courses.values()
        for course in courses
        if course and course.strip()
    }
    missing_courses = sorted(selected_courses - configured_courses)
    if missing_courses:
        raise ValueError(
            "Every selected course must have a section count. Missing: "
            + ", ".join(missing_courses)
        )


def _apply_scheduler_inputs(
    scheduler: Scheduler_Final,
    block_ban_map: Optional[Dict[str, List[int]]] = None,
    forbidden_course_groups: Optional[List[List[str]]] = None,
    min_students_threshold: Optional[int] = None,
    max_students_threshold: Optional[int] = None,
    min_students_per_section: Optional[int] = None,
    max_students_per_section: Optional[int] = None,
    cost_weights: Optional[Dict[str, float]] = None,
) -> None:
    if block_ban_map is not None:
        scheduler.block_ban_map = {
            course: {int(slot) for slot in slots}
            for course, slots in block_ban_map.items()
        }

    if forbidden_course_groups is not None:
        scheduler.forbidden_course_groups = {
            tuple(course for course in group if course and course.strip())
            for group in forbidden_course_groups
            if len(group) >= 2
        }

    if min_students_threshold is not None:
        scheduler.MIN_STUDENTS_THRESHOLD = int(min_students_threshold)
    if max_students_threshold is not None:
        scheduler.MAX_STUDENTS_THRESHOLD = int(max_students_threshold)
    if min_students_per_section is not None:
        scheduler.MIN_STUDENTS_PER_SECTION = int(min_students_per_section)
    if max_students_per_section is not None:
        scheduler.MAX_STUDENTS_PER_SECTION = int(max_students_per_section)

    weight_map = {
        "conflict": "W_CONFLICT",
        "timeslot": "W_TIMESLOT",
        "section": "W_SECTION",
        "excess_variance": "W_EXCESS_VARIANCE",
        "min_students": "W_MIN_STUDENTS",
    }
    for key, attribute in weight_map.items():
        if cost_weights and key in cost_weights:
            setattr(scheduler, attribute, float(cost_weights[key]))


def arrange_courses(
    student_courses: Dict[Any, List[str]],
    section_counts: Dict[str, int],
    num_time_slots: int = 7,
    block_ban_map: Optional[Dict[str, List[int]]] = None,
    forbidden_course_groups: Optional[List[List[str]]] = None,
    course_name_map: Optional[Dict[str, str]] = None,
    min_students_threshold: int = 25,
    max_students_threshold: int = 65,
    min_students_per_section: int = 12,
    max_students_per_section: int = 30,
    cost_weights: Optional[Dict[str, float]] = None,
    max_iterations: int = 20000,
    seed: Optional[int] = None,
    export_csv_path: Optional[str] = None,
    include_schedule: bool = True,
    include_section_loads: bool = True,
    include_diagnostics: bool = False,
    verbose: bool = False,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """OpenAI tool handler for generating a course timetable.

    This function is intentionally JSON-serializable so its return value can be
    sent directly back to the OpenAI API as tool output.
    """
    if not 2 <= num_time_slots <= 12:
        raise ValueError("num_time_slots must be between 2 and 12.")
    if not 100 <= max_iterations <= 100000:
        raise ValueError("max_iterations must be between 100 and 100000.")

    if seed is not None:
        random.seed(seed)
        np.random.seed(seed)

    course_name_map = course_name_map or {}
    student_data = _normalize_student_courses(student_courses)
    custom_sections = _normalize_section_counts(section_counts)
    _validate_course_coverage(student_data, custom_sections, course_name_map)

    scheduler = Scheduler_Final(
        num_time_slots=num_time_slots,
        max_iterations=max_iterations,
        cancel_check=cancel_check,
    )
    _apply_scheduler_inputs(
        scheduler,
        block_ban_map=block_ban_map if block_ban_map is not None else {},
        forbidden_course_groups=forbidden_course_groups if forbidden_course_groups is not None else [],
        min_students_threshold=min_students_threshold,
        max_students_threshold=max_students_threshold,
        min_students_per_section=min_students_per_section,
        max_students_per_section=max_students_per_section,
        cost_weights=cost_weights,
    )

    log_buffer = io.StringIO()
    output_context = contextlib.nullcontext() if verbose else contextlib.redirect_stdout(log_buffer)
    with output_context:
        scheduler._clean_and_prepare_data(student_data, custom_sections, course_name_map=course_name_map)
        optimal_schedule = scheduler.optimize_schedule()

    cost, details = scheduler._evaluate_schedule_with_balance(optimal_schedule)
    satisfaction_rate = (
        (len(scheduler.student_courses) - details["conflict_count"])
        / len(scheduler.student_courses)
        * 100
    )
    slot_loads = Counter(optimal_schedule.values())
    section_loads = scheduler._get_simulated_student_loads(optimal_schedule)

    result: Dict[str, Any] = {
        "student_count": len(scheduler.student_courses),
        "course_count": len(scheduler.course_sections_map),
        "section_count": len(scheduler.all_sections),
        "num_time_slots": scheduler.num_time_slots,
        "metrics": {
            "satisfaction_rate": round(float(satisfaction_rate), 4),
            "conflict_count": int(details["conflict_count"]),
            "timeslot_variance": round(float(details["timeslot_variance"]), 4),
            "section_variance": round(float(details["section_variance"]), 4),
            "min_students_violations": round(float(details.get("min_students_violations", 0)), 4),
            "total_cost": round(float(cost), 4),
        },
        "time_slot_section_counts": {
            str(slot): int(slot_loads.get(slot, 0)) for slot in range(scheduler.num_time_slots)
        },
        "course_slot_matrix": _course_slot_matrix(scheduler, optimal_schedule),
    }

    if include_schedule:
        result["schedule_by_slot"] = _schedule_by_slot(scheduler, optimal_schedule)

    if include_section_loads:
        result["section_loads"] = {
            section: int(section_loads.get(section, 0)) for section in sorted(optimal_schedule)
        }

    if include_diagnostics:
        diagnostic_buffer = io.StringIO()
        with contextlib.redirect_stdout(diagnostic_buffer):
            scheduler.diagnose_conflicts(optimal_schedule)
        result["diagnostics"] = diagnostic_buffer.getvalue()

    if export_csv_path:
        with contextlib.redirect_stdout(io.StringIO()):
            scheduler.export_partition_matrix_csv(optimal_schedule, export_csv_path)
        result["csv_export_path"] = export_csv_path

    if not verbose:
        result["optimizer_log"] = log_buffer.getvalue()

    return result


def _tool_call_name_and_arguments(tool_call: Any) -> Tuple[str, Dict[str, Any]]:
    if isinstance(tool_call, dict):
        if "function" in tool_call:
            function_data = tool_call["function"]
            name = function_data.get("name")
            arguments = function_data.get("arguments", "{}")
        else:
            name = tool_call.get("name")
            arguments = tool_call.get("arguments", "{}")
    elif hasattr(tool_call, "function"):
        name = tool_call.function.name
        arguments = tool_call.function.arguments
    else:
        name = tool_call.name
        arguments = tool_call.arguments

    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")

    return name, arguments


def execute_tool_call(tool_call: Any) -> str:
    """Execute an OpenAI SDK tool call and return JSON text for tool output."""
    name, arguments = _tool_call_name_and_arguments(tool_call)
    if name != "arrange_courses":
        raise ValueError(f"Unsupported tool call: {name}")

    result = arrange_courses(**arguments)
    return json.dumps(result, ensure_ascii=False)
