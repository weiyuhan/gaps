from __future__ import print_function
from operator import attrgetter
from gaps import image_helpers
from gaps.selection import roulette_selection
from gaps.plot import Plot
from gaps.progress_bar import print_progress
from gaps.crowd.crossover import Crossover
from gaps.crowd.individual import Individual
from gaps.crowd.image_analysis import ImageAnalysis
from gaps.crowd.fitness import db_update


# Don't create two instantces for this class
class GeneticAlgorithm(object):

    TERMINATION_THRESHOLD = 10

    def __init__(self, image, piece_size, population_size, generations, elite_size=2):
        self._image = image
        self._piece_size = piece_size
        self._generations = generations
        self._elite_size = elite_size
        pieces, rows, columns = image_helpers.flatten_image(image, piece_size, indexed=True)
        self._population = [Individual(pieces, rows, columns) for _ in range(population_size)]
        self._pieces = pieces

    def start_evolution(self, verbose):
        print("=== Pieces:      {}\n".format(len(self._pieces)))

        if verbose:
            plot = Plot(self._image)

        #ImageAnalysis.analyze_image(self._pieces)

        fittest = None
        best_fitness_score = float("-inf")
        termination_counter = 0

        for generation in range(self._generations):
            print_progress(generation, self._generations - 1, prefix="=== Solving puzzle: ")

            ## In crowd-based algorithm, we need to access database to updata fintess measure
            ## at the beginning of each generation.
            # update fitness from database.
            db_update()
            # calculate dissimilarity and best_match_table.
            ImageAnalysis.analyze_image(self._pieces)
            # fitness of all individuals need to be re-calculated.
            for _individual in self._population:
                _individual._fitness = None

            new_population = []

            # Elitism
            elite = self._get_elite_individuals(elites=self._elite_size)
            new_population.extend(elite)

            selected_parents = roulette_selection(self._population, elites=self._elite_size)

            for first_parent, second_parent in selected_parents:
                crossover = Crossover(first_parent, second_parent)
                crossover.run()
                child = crossover.child()
                if child.is_solution():
                    """
                    write database
                    send HTTP message to server
                    terminate this process
                    """
                    pass
                new_population.append(child)

            fittest = self._best_individual()

            if fittest.fitness <= best_fitness_score:
                termination_counter += 1
            else:
                best_fitness_score = fittest.fitness

            if termination_counter == self.TERMINATION_THRESHOLD:
                print("\n\n=== GA terminated")
                print("=== There was no improvement for {} generations".format(self.TERMINATION_THRESHOLD))
                return fittest

            self._population = new_population

            if verbose:
                plot.show_fittest(fittest.to_image(), "Generation: {} / {}".format(generation + 1, self._generations))

        return fittest

    def _get_elite_individuals(self, elites):
        """Returns first 'elite_count' fittest individuals from population"""
        return sorted(self._population, key=attrgetter("fitness"))[-elites:]

    def _best_individual(self):
        """Returns the fittest individual from population"""
        return max(self._population, key=attrgetter("fitness"))
