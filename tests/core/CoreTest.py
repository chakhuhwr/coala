import unittest

from coalib.settings.Section import Section
from coalib.core.Bear import Bear
from coalib.core.Core import initialize_dependencies, run

from coala_utils.decorators import generate_eq


# Classes are hashed by instance, so they can be placed inside a set, compared
# to normal tuples which hash their contents. This allows to pass the file-dict
# into results.
@generate_eq('bear', 'section_name', 'file_dict')
class TestResult:
    def __init__(self, bear, section_name, file_dict):
        self.bear = bear
        self.section_name = section_name
        self.file_dict = file_dict


class TestBear(Bear):
    DEPENDENCIES = set()

    def analyze(self, bear, section_name, file_dict):
        # The bear can in fact return everything (so it's not bound to actual
        # `Result`s), but it must be at least an iterable.
        return [TestResult(bear, section_name, file_dict)]

    def generate_tasks(self):
        # Choose single task parallelization for simplicity. Also use the
        # section name as a parameter instead of the section itself, as compare
        # operations on tests do not succeed on them due to the pickling of
        # multiprocessing to transfer objects to the other process, which
        # instantiates a new section on each transfer.
        return ((self, self.section.name, self.file_dict), {}),


class MultiParallelizationBear(Bear):
    DEPENDENCIES = set()

    def analyze(self, run_id):
        return [run_id]

    def generate_tasks(self):
        # Choose single task parallelization for simplicity. Also use the
        # section name as a parameter instead of the section itself, as compare
        # operations on tests do not succeed on them due to the pickling of
        # multiprocessing to transfer objects to the other process, which
        # instantiates a new section on each transfer.
        return (((i,), {}) for i in range(3))


class BearA(TestBear):
    pass


class BearB(TestBear):
    pass


class BearC_NeedsB(TestBear):
    DEPENDENCIES = {BearB}


class BearD_NeedsC(TestBear):
    DEPENDENCIES = {BearC_NeedsB}


class BearE_NeedsAD(TestBear):
    DEPENDENCIES = {BearA, BearD_NeedsC}


class FailingBear(TestBear):
    def analyze(self, bear, section_name, file_dict):
        raise ValueError


class BearF_NeedsFailingBear(TestBear):
    DEPENDENCIES = {FailingBear}


class BearG_NeedsF(TestBear):
    DEPENDENCIES = {BearF_NeedsFailingBear}


def get_next_instance(iterable, typ):
    """
    Reads all elements in the iterable and returns the first occurrence
    that is an instance of given type.

    :param iterable:
        The iterable to search in.
    :param typ:
        The type an object shall have.
    :return:
        The instance having ``typ`` or ``None`` if not found in
        ``iterable``.
    """
    try:
        return next(obj for obj in iterable if isinstance(obj, typ))
    except StopIteration:
        return None


class InitializeDependenciesTest(unittest.TestCase):
    def test_multi_dependencies(self):
        # General test which makes use of the full dependency chain from the
        # defined classes above.
        section = Section('test-section')
        filedict = {}

        bear_e = BearE_NeedsAD(section, filedict)
        dependency_tracker, bears_to_schedule = initialize_dependencies(
            {bear_e})

        self.assertEqual(len(dependency_tracker.get_dependencies(bear_e)), 2)
        self.assertTrue(any(isinstance(bear, BearA) for bear in
                            dependency_tracker.get_dependencies(bear_e)))
        self.assertTrue(any(isinstance(bear, BearD_NeedsC) for bear in
                            dependency_tracker.get_dependencies(bear_e)))

        # Test path BearE -> BearA.
        bear_a = get_next_instance(
            dependency_tracker.get_dependencies(bear_e), BearA)

        self.assertIsNotNone(bear_a)
        self.assertIs(bear_a.section, section)
        self.assertIs(bear_a.file_dict, filedict)
        self.assertEqual(dependency_tracker.get_dependencies(bear_a), set())

        # Test path BearE -> BearD.
        bear_d = get_next_instance(
            dependency_tracker.get_dependencies(bear_e), BearD_NeedsC)

        self.assertIsNotNone(bear_d)
        self.assertIs(bear_d.section, section)
        self.assertIs(bear_a.file_dict, filedict)
        self.assertEqual(len(dependency_tracker.get_dependencies(bear_d)), 1)

        # Test path BearE -> BearD -> BearC.
        self.assertEqual(len(dependency_tracker.get_dependencies(bear_d)), 1)

        bear_c = dependency_tracker.get_dependencies(bear_d).pop()

        self.assertIs(bear_c.section, section)
        self.assertIs(bear_a.file_dict, filedict)
        self.assertIsInstance(bear_c, BearC_NeedsB)

        # Test path BearE -> BearD -> BearC -> BearB.
        self.assertEqual(len(dependency_tracker.get_dependencies(bear_c)), 1)

        bear_b = dependency_tracker.get_dependencies(bear_c).pop()

        self.assertIs(bear_b.section, section)
        self.assertIs(bear_a.file_dict, filedict)
        self.assertIsInstance(bear_b, BearB)

        # No more dependencies after BearB.
        self.assertEqual(dependency_tracker.get_dependencies(bear_b), set())

        # Finally check the bears_to_schedule
        self.assertEqual(bears_to_schedule, {bear_a, bear_b})

    def test_simple(self):
        # Test simple case without dependencies.
        section = Section('test-section')
        filedict = {}

        bear_a = BearA(section, filedict)
        bear_b = BearB(section, filedict)

        dependency_tracker, bears_to_schedule = initialize_dependencies(
            {bear_a, bear_b})

        self.assertTrue(dependency_tracker.all_dependencies_resolved)
        self.assertEqual(bears_to_schedule, {bear_a, bear_b})

    def test_reuse_instantiated_dependencies(self):
        # Test whether pre-instantiated dependency bears are correctly
        # (re)used.
        section = Section('test-section')
        filedict = {}

        bear_b = BearB(section, filedict)
        bear_c = BearC_NeedsB(section, filedict)

        dependency_tracker, bears_to_schedule = initialize_dependencies(
            {bear_b, bear_c})

        self.assertEqual(dependency_tracker.dependants, {bear_c})
        self.assertEqual(dependency_tracker.get_dependencies(bear_c), {bear_b})

        self.assertEqual(bears_to_schedule, {bear_b})

    def test_no_reuse_of_different_section_dependency(self):
        # Test whether pre-instantiated bears which belong to different
        # sections are not (re)used, as the sections are different.
        section1 = Section('test-section1')
        section2 = Section('test-section2')
        filedict = {}

        bear_b = BearB(section1, filedict)
        bear_c = BearC_NeedsB(section2, filedict)

        dependency_tracker, bears_to_schedule = initialize_dependencies(
            {bear_b, bear_c})

        self.assertEqual(dependency_tracker.dependants, {bear_c})
        dependencies = dependency_tracker.dependencies
        self.assertEqual(len(dependencies), 1)
        dependency = dependencies.pop()
        self.assertIsInstance(dependency, BearB)
        self.assertIsNot(dependency, bear_b)

        self.assertEqual(bears_to_schedule, {bear_b, dependency})

    def test_different_sections_different_dependency_instances(self):
        # Test whether two bears of same type but different sections get their
        # own dependency bear instances.
        section1 = Section('test-section1')
        section2 = Section('test-section2')
        filedict = {}

        bear_c_section1 = BearC_NeedsB(section1, filedict)
        bear_c_section2 = BearC_NeedsB(section2, filedict)

        dependency_tracker, bears_to_schedule = initialize_dependencies(
            {bear_c_section1, bear_c_section2})

        # Test path for section1
        bear_c_s1_dependencies = dependency_tracker.get_dependencies(
            bear_c_section1)
        self.assertEqual(len(bear_c_s1_dependencies), 1)
        bear_b_section1 = bear_c_s1_dependencies.pop()
        self.assertIsInstance(bear_b_section1, BearB)

        # Test path for section2
        bear_c_s2_dependencies = dependency_tracker.get_dependencies(
            bear_c_section2)
        self.assertEqual(len(bear_c_s2_dependencies), 1)
        bear_b_section2 = bear_c_s2_dependencies.pop()
        self.assertIsInstance(bear_b_section2, BearB)

        # Test if both dependencies aren't the same.
        self.assertIsNot(bear_b_section1, bear_b_section2)

        # Test bears for schedule.
        self.assertEqual(bears_to_schedule, {bear_b_section1, bear_b_section2})

    def test_reuse_multiple_same_dependencies_correctly(self):
        # Test whether two pre-instantiated dependencies with the same section
        # and file-dictionary are correctly registered as dependencies, so only
        # a single one of those instances should be picked as a dependency.
        section = Section('test-section')
        filedict = {}

        bear_c = BearC_NeedsB(section, filedict)
        bear_b1 = BearB(section, filedict)
        bear_b2 = BearB(section, filedict)

        dependency_tracker, bears_to_schedule = initialize_dependencies(
            {bear_c, bear_b1, bear_b2})

        bear_c_dependencies = dependency_tracker.get_dependencies(bear_c)
        self.assertEqual(len(bear_c_dependencies), 1)
        bear_c_dependency = bear_c_dependencies.pop()
        self.assertIsInstance(bear_c_dependency, BearB)

        self.assertIn(bear_c_dependency, {bear_b1, bear_b2})

        self.assertEqual(bears_to_schedule, {bear_b1, bear_b2})

    def test_correct_reuse_of_implicitly_instantiated_dependency(self):
        # Test if a single dependency instance is created for two different
        # instances pointing to the same section and file-dictionary.
        section = Section('test-section')
        filedict = {}

        bear_c1 = BearC_NeedsB(section, filedict)
        bear_c2 = BearC_NeedsB(section, filedict)

        dependency_tracker, bears_to_schedule = initialize_dependencies(
            {bear_c1, bear_c2})

        # Test first path.
        bear_c1_dependencies = dependency_tracker.get_dependencies(bear_c1)
        self.assertEqual(len(bear_c1_dependencies), 1)
        bear_b1 = bear_c1_dependencies.pop()
        self.assertIsInstance(bear_b1, BearB)

        # Test second path.
        bear_c2_dependencies = dependency_tracker.get_dependencies(bear_c2)
        self.assertEqual(len(bear_c2_dependencies), 1)
        bear_b2 = bear_c2_dependencies.pop()
        self.assertIsInstance(bear_b2, BearB)

        # Test if both dependencies are actually the same.
        self.assertIs(bear_b1, bear_b2)

    def test_empty_case(self):
        # Test totally empty case.
        dependency_tracker, bears_to_schedule = initialize_dependencies(set())

        self.assertTrue(dependency_tracker.all_dependencies_resolved)
        self.assertEqual(bears_to_schedule, set())

    def test_different_filedict_different_dependency_instance(self):
        # Test whether pre-instantiated bears which have different
        # file-dictionaries assigned are not (re)used, as they have different
        # file-dictionaries.
        section = Section('test-section')
        filedict1 = {'f2': []}
        filedict2 = {'f1': []}

        bear_b = BearB(section, filedict1)
        bear_c = BearC_NeedsB(section, filedict2)

        dependency_tracker, bears_to_schedule = initialize_dependencies(
            {bear_b, bear_c})

        self.assertEqual(dependency_tracker.dependants, {bear_c})
        dependencies = dependency_tracker.dependencies
        self.assertEqual(len(dependencies), 1)
        dependency = dependencies.pop()
        self.assertIsInstance(dependency, BearB)
        self.assertIsNot(dependency, bear_b)

        self.assertEqual(bears_to_schedule, {bear_b, dependency})


class CoreTest(unittest.TestCase):
    @staticmethod
    def execute_run(bears):
        results = []

        def on_result(result):
            results.append(result)

        run(bears, on_result)

        return results

    def test_run_simple(self):
        # Test single bear without dependencies case.
        section = Section('test-section')
        filedict = {}

        bear_a = BearA(section, filedict)

        results = self.execute_run({bear_a})

        self.assertEqual(
            list((result.bear.name, result.section_name, result.file_dict)
                 for result in results),
            [('BearA', section.name, filedict)])

    def test_run_complex(self):
        # Run a complete dependency chain.
        section = Section('test-section')
        filedict = {}

        bear_e = BearE_NeedsAD(section, filedict)

        results = self.execute_run({bear_e})

        # Test if the section name and filedict are all the same.
        for result in results:
            self.assertEqual(result.section_name, section.name)
            self.assertEqual(result.file_dict, filedict)

        # The last bear executed has to be BearE_NeedsAD.
        self.assertEqual(results[-1].bear.name, bear_e.name)

        bear_names_executed = {result.bear.name for result in results}

        self.assertEqual(len(bear_names_executed), len(results))
        self.assertEqual(
            bear_names_executed,
            {BearE_NeedsAD.name, BearD_NeedsC.name, BearC_NeedsB.name,
             BearA.name, BearB.name})

        # Check dependency results.
        self.assertEqual(len(bear_e.dependency_results), 2)

        test_results = [
            (result.bear.name, result.section_name, result.file_dict)
            for result in bear_e.dependency_results]

        for result in [(BearA.name, section.name, filedict),
                       (BearD_NeedsC.name, section.name, filedict)]:
            self.assertIn(result, test_results)

        bear_a = next(result.bear
                      for result in results
                      if isinstance(result.bear, BearA))

        self.assertEqual(tuple(), bear_a.dependency_results)

        bear_d = next(result.bear
                      for result in results
                      if isinstance(result.bear, BearD_NeedsC))

        test_results = [
            (result.bear.name, result.section_name, result.file_dict)
            for result in bear_d.dependency_results]

        self.assertEqual(
            test_results,
            [(BearC_NeedsB.name, section.name, filedict)])

        bear_c = next(result.bear
                      for result in results
                      if isinstance(result.bear, BearC_NeedsB))

        test_results = [
            (result.bear.name, result.section_name, result.file_dict)
            for result in bear_c.dependency_results]

        self.assertEqual(
            test_results,
            [(BearB.name, section.name, filedict)])

        bear_b = next(result.bear
                      for result in results
                      if isinstance(result.bear, BearB))

        self.assertEqual(tuple(), bear_b.dependency_results)

    def test_run3(self):
        # Test exception in result handler. The core needs to retry to invoke
        # the handler and then exit correctly if no more results and bears are
        # left.
        # TODO yield multiple results to test retrial. Use mock for that.
        bear_a = BearA(Section('test-section'), {})

        def on_result(result):
            raise ValueError

        run({bear_a}, on_result)

    def test_run4(self):
        # Test exception in bear. Core needs to shutdown directly and not wait
        # forever.
        self.execute_run({FailingBear(Section('test-section'), {})})

    def test_run5(self):
        # Test when bear is not completely finished because it has multiple
        # tasks.
        bear = MultiParallelizationBear(Section('test-section'), {})

        results = self.execute_run({bear})

        result_set = set(results)
        self.assertEqual(len(result_set), len(results))
        self.assertEqual(result_set, {0, 1, 2})
        # TODO Test this with dependencies, whether they get resolved
        # TODO   correctly.

    def test_run6(self):
        # Test when bear with dependants crashes. Dependent bears need to be
        # unscheduled and remaining non-related bears shall continue execution.
        section = Section('test-section')
        filedict = {}

        bear_a = BearA(section, filedict)
        bear_failing = BearG_NeedsF(section, filedict)

        # bear_failing's dependency will fail, so there should only be results
        # from bear_a.
        results = self.execute_run({bear_a, bear_failing})

        test_results = [
            (result.bear.name, result.section_name, result.file_dict)
            for result in results]

        self.assertEqual(test_results,
                         [(BearA.name, section.name, filedict)])

        self.assertEqual(bear_failing.dependency_results, tuple())

    # TODO Test heavy setup, multiple instances with same and different
    # TODO   sections/file-dicts.

    # TODO test dependency result passing, and also try to generate tasks
    # TODO   dynamically from dependency-results! And test that
    # TODO `self.dependency_results == None` when no deps provided.

    """
    Traceback (most recent call last):
  File "/usr/lib64/python3.5/multiprocessing/queues.py", line 241, in _feed
    obj = ForkingPickler.dumps(obj)
  File "/usr/lib64/python3.5/multiprocessing/reduction.py", line 50, in dumps
    cls(buf, protocol).dump(obj)
  AttributeError: Can't pickle local object
    'CoreTest.test_run6.<locals>.FailingBear'
    """
    # TODO BUG -> Shouldn't this happen on the main thread? why does the core
    # TODO   block then forever? Huh, when redefining classes, they can't be
    # TODO   pickled any more... interesting... still unpicklibility shall not
    # TODO   hang up the core^^

    # TODO test that get_all_dependants() is better than get_dependants()