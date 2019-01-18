#!/usr/local/bin/python3

from argparse import ArgumentParser, ArgumentTypeError
import csv
import datetime
from enum import Enum, auto
import os
from distutils.util import strtobool
from math import isclose
import sys
import time

LOG_FILE_PATH = os.path.join(os.path.expanduser('~'), '.hour_logger', 'log.csv')

class ModeFailException(Exception):
    pass

def prompt_until_success(question, parser_fn):
    while True:
        print(question, end='')
        try:
            return parser_fn(input())
        except ValueError:
            print('Not a valid response.')

def script_path():
    return os.path.realpath(__file__)

def script_name():
    return os.path.basename(__file__)

class LogEvent(Enum):
    WAGE_SET = auto()
    PAYMENT = auto()
    START = auto()
    END = auto()

def positive_float(val):
    num = float(val)
    if num < 0:
        raise ValueError(f'{val} is a negative number.')
    return num

class LogReport:
    def __init__(self, active_wage=None, current_shift_started_at=None, total_earned=0, total_paid=0):
        self.active_wage = active_wage
        self.current_shift_started_at = current_shift_started_at
        self.total_earned = total_earned
        self.total_paid = total_paid

    @property
    def outstanding_payment(self):
        return self.total_earned - self.total_paid

    @property
    def has_outstanding_payment(self):
        return not isclose(self.total_earned, self.total_paid, abs_tol=0.01)

    @property
    def in_shift(self):
        return self.current_shift_started_at != None

    @property
    def has_active_wage(self):
        return self.active_wage != None

    @property
    def current_shift_duration(self):
        if self.current_shift_started_at is None:
            return None
        else:
            duration = time.time() - self.current_shift_started_at
            if duration < 0:
                raise ModeFailException(f'Log file at {LOG_FILE_PATH} is corrupted; the ongoing shift seems to have been started in the future.')
            return duration

def prepare_report():
    report = LogReport()
    
    for event, value in read_log():
        if event == LogEvent.WAGE_SET:
            report.active_wage = value
        elif event == LogEvent.PAYMENT:
            report.total_paid += value
        elif event == LogEvent.START: 
            if report.in_shift:
                raise ModeFailException(f'Log file at {LOG_FILE_PATH} is corrupted; found two successive {LogEvent.START.name}s without a {LogEvent.END.name} in between. Try fixing or deleting it.')
            if report.active_wage is None:
                raise ModeFailException(f'Log file at {LOG_FILE_PATH} is corrupted; A shift {event.name} event occurred before any {LogEvent.WAGE_SET.name} event.')
            report.current_shift_started_at = value
        elif event == LogEvent.END:
            if not report.in_shift:
                raise ModeFailException(f'Log file at {LOG_FILE_PATH} is corrupted; found two successive {LogEvent.END.name}s without a {LogEvent.START.name} in between. Try fixing or deleting it.')
            if report.active_wage is None:
                raise ModeFailException(f'Log file at {LOG_FILE_PATH} is corrupted; A shift {event.name} event occurred before any {LogEvent.WAGE_SET.name} event.')
            
            seconds = value - report.current_shift_started_at
            report.current_shift_started_at = None
            if (seconds < 0):
                raise ModeFailException(f'Log file at {LOG_FILE_PATH} is corrupted; A shift\'s duration cannot be negative. Try fixing or deleting it.')
            
            report.total_earned += (seconds/60/60) * report.active_wage
        else:
            assert False, f'Support for new LogEvent {event.name} not added.'

    return report


def read_log():
    with open(LOG_FILE_PATH, 'r') as log_file:
        csv_reader = csv.reader(log_file)
        for log in csv_reader:
            event = next((e for e in LogEvent if e.name == log[0]), None)
            if event is None:
                raise ModeFailException(f'Log file at {LOG_FILE_PATH} is corrupted; found an unknown log event: {log}')
            value = float(log[1])
            yield event, value

def write_log(event, value):
    with open(LOG_FILE_PATH, 'a') as log_file:
        csv_writer = csv.writer(log_file)
        csv_writer.writerow([event.name, value])

def read_sanitized_report(expected_in_shift=None, if_shift_err=None):
    if (expected_in_shift is None and if_shift_err is not None) or (expected_in_shift is not None and if_shift_err is None):
        raise ValueError('Either both, or neither of expected_in_shift and if_shift_err should be null.')

    report = prepare_report()
    if not report.has_active_wage:
        raise ModeFailException(f'Log file at {LOG_FILE_PATH} is corrupted; no {LogEvent.WAGE_SET.name} events found. Try fixing or deleting it.')

    if expected_in_shift is not None and report.in_shift != expected_in_shift:
        raise ModeFailException(if_shift_err)

    return report

def configure_as_new():
    should_configure = prompt_until_success(question=f'Looks like you have never configured {script_name()} before. Would you like to do so now? [y/n] ', parser_fn=lambda x: strtobool(x) == 1)
    if not should_configure:
        raise ModeFailException(f'{script_name()} cannot run without configuring.')

    wage = prompt_until_success(question='What is your hourly wage? ', parser_fn=float)

    if not os.path.exists(os.path.dirname(LOG_FILE_PATH)):
        os.makedirs(os.path.dirname(LOG_FILE_PATH))

    write_log(LogEvent.WAGE_SET, wage)

    print(f'Log log file created at: {LOG_FILE_PATH}.')

    return LogReport(active_wage=wage)

class App:
    class Mode:
        def __init__(self, name, runner, help, arg_type=None):
            self.name = name
            self.runner = runner
            self.arg_type = arg_type
            self.help = help

    def __init__(self):
        self.registered_modes = []

    def add_mode(self, mode):
        self.registered_modes.append(mode)

    def run(self):
        if len(self.registered_modes) == 0:
            raise ValueError('No modes were registered')

        parser = ArgumentParser(description='A tool for managing your work hours and the money you made.')
        group = parser.add_mutually_exclusive_group()

        for mode in self.registered_modes:
            if mode.arg_type is None:
                group.add_argument(f'-{mode.name[0]}', f'--{mode.name}', action='store_true', help=mode.help)
            else: 
                group.add_argument(f'-{mode.name[0]}', f'--{mode.name}', type=mode.arg_type, help=mode.help)

        args = parser.parse_args()

        matching_mode = next((mode for mode in self.registered_modes if not not getattr(args, mode.name)), self.registered_modes[0])
        try:
            if matching_mode.arg_type is None:
                matching_mode.runner()
                return 0
            else:
                matching_mode.runner(getattr(args, matching_mode.name))
                return 0
        except ModeFailException as e:
            print(str(e))
            return 3

app = App()

def register_mode(expected_in_shift=None, if_shift_err=None, help=None):
    def _register_mode(mode_fn):
        class ModeParamData:
            def __init__(self, index, name, type):
                self.index = index
                self.name = name
                self.type = type
        
        assert len(mode_fn.__annotations__) <= 2, 'mode functions can only either accept 1 command line arg, the current log report, or both.'
        
        report_param_data = next((ModeParamData(index=i, name=param[0], type=param[1]) for i, param in enumerate(mode_fn.__annotations__.items()) if param[1] == LogReport), None)
        cli_param_data = next((ModeParamData(index=i, name=param[0], type=param[1]) for i, param in enumerate(mode_fn.__annotations__.items()) if param[1] != LogReport), None)

        def wrapper(*args):
            if os.path.isfile(LOG_FILE_PATH):
                report = read_sanitized_report(expected_in_shift, if_shift_err)
            else:
                report = configure_as_new()

            kwargs = dict()
            if cli_param_data is not None:
                kwargs[cli_param_data.name] = args[0]

            if report_param_data is not None:
                kwargs[report_param_data.name] = report
            
            mode_fn(**kwargs)

        app.add_mode(App.Mode(name=mode_fn.__name__, runner=wrapper, help=help, arg_type=cli_param_data.type if cli_param_data is not None else None))
        return wrapper
    return _register_mode

@register_mode(help='see the current status summary in a bitbar compatible syntax')
def bitbar(report: LogReport):
    if report.in_shift:
        print(f'🕒 {datetime.timedelta(seconds=report.current_shift_duration)}')
    else:
        print('🏠')

    print('---')
    if report.in_shift:
        print(f'End Shift | refresh=true bash="{script_path()}" param1=--end terminal=false')
    else:
        print(f'Start Shift | refresh=true bash="{script_path()}" param1=--start terminal=false')

    print(f'Open log | refresh=true bash="less" param1={LOG_FILE_PATH} terminal=true')

    if report.has_outstanding_payment:
        print('---')
        if report.outstanding_payment > 0:
            print(f'💰 {report.outstanding_payment:.2f} pending')
        else:
            print(f'💰 {-report.outstanding_payment:.2f} overpaid')

@register_mode(help='see the current status summary info')
def info(report: LogReport):
    if report.in_shift:
        print(f'🕒 {datetime.timedelta(seconds=report.current_shift_duration)}', end='')
    else:
        print('🏠', end='')

    if report.has_outstanding_payment:
        print(' | ')
        if report.outstanding_payment > 0:
            print(f'💰 {report.outstanding_payment:.2f} pending', end='')
        else:
            print(f'💰 {-report.outstanding_payment:.2f} overpaid', end='')
    print()

@register_mode(expected_in_shift=False, if_shift_err='Cannot change the wage while a shift is ongoing.', help='update the hourly wage moving forward; must be non-negative')
def wage(wage: positive_float):
    write_log(LogEvent.WAGE_SET, wage)

@register_mode(help='add a received payment; must be non-negative')
def payment(amount: positive_float):
    write_log(LogEvent.PAYMENT, amount)

@register_mode(expected_in_shift=False, if_shift_err='Cannot start a shift while one is ongoing.', help='start a shift')
def start():
    write_log(LogEvent.START, time.time())

@register_mode(expected_in_shift=True, if_shift_err='Cannot end a shift when none is ongoing.', help='end a shift')
def end():
    write_log(LogEvent.END, time.time())

if __name__ == '__main__':
    sys.exit(app.run())