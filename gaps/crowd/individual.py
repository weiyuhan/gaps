import numpy as np
from gaps import image_helpers
from gaps.crowd.image_analysis import ImageAnalysis
from gaps.config import Config
from gaps.crowd.fitness import db_update


class Individual(object):
    """Class representing possible solution to puzzle.

    Individual object is one of the solutions to the problem
    (possible arrangement of the puzzle's pieces).
    It is created by random shuffling initial puzzle.

    :param pieces:  Array of pieces representing initial puzzle.
    :param rows:    Number of rows in input puzzle
    :param columns: Number of columns in input puzzle

    Usage::

        >>> from gaps.individual import Individual
        >>> from gaps.image_helpers import flatten_image
        >>> pieces, rows, columns = flatten_image(...)
        >>> ind = Individual(pieces, rows, columns)

    """

    # FITNESS_FACTOR = 1000

    def __init__(self, pieces, rows, columns, shuffle=True):
        self.pieces = pieces[:]
        self.rows = rows
        self.columns = columns
        self._objective = None
        self._fitness = None
        self._is_solution = None

        if shuffle:
            np.random.shuffle(self.pieces)

        # Map piece ID to index in Individual's list
        self._piece_mapping = {piece.id: index for index, piece in enumerate(self.pieces)}

    def __getitem__(self, key):
        return self.pieces[key * self.columns:(key + 1) * self.columns]

    @property
    def objective(self):
        if self._objective is None:
            objective_value = 0
            # For each two adjacent pieces in rows
            for i in range(self.rows):
                for j in range(self.columns - 1):
                    ids = (self[i][j].id, self[i][j + 1].id)
                    objective_value += -ImageAnalysis.get_dissimilarity(ids, orientation="LR")
            # For each two adjacent pieces in columns
            for i in range(self.rows - 1):
                for j in range(self.columns):
                    ids = (self[i][j].id, self[i + 1][j].id)
                    objective_value += -ImageAnalysis.get_dissimilarity(ids, orientation="TD")
            self._objective = objective_value
        return self._objective

    @property
    def fitness(self):
        """Evaluates fitness value.

        Fitness value is calculated as sum of dissimilarity measures between each adjacent pieces.

        """
        '''
        if self._fitness is None:
            fitness_value = 1 / self.FITNESS_FACTOR
            # For each two adjacent pieces in rows
            for i in range(self.rows):
                for j in range(self.columns - 1):
                    ids = (self[i][j].id, self[i][j + 1].id)
                    fitness_value += ImageAnalysis.get_dissimilarity(ids, orientation="LR")
            # For each two adjacent pieces in columns
            for i in range(self.rows - 1):
                for j in range(self.columns):
                    ids = (self[i][j].id, self[i + 1][j].id)
                    fitness_value += ImageAnalysis.get_dissimilarity(ids, orientation="TD")

            self._fitness = self.FITNESS_FACTOR / fitness_value   

        return self._fitness
        '''
        """ Evaluate fitness value with crowd-based measure.
        """          
        if self._fitness is None or self._objective is None:
            self._fitness = Config.fitness_func(self.objective)

        return self._fitness

    def edges_set(self):
        edges = set()
        for index in range(len(self.pieces)):
            if index % self.columns < self.columns - 1:
                edge = str(self.pieces[index].id) + 'L-R' + str(self.pieces[index + 1].id)
                edges.add(edge)
            if index < (self.rows - 1) * self.columns:
                edge = str(self.pieces[index].id) + 'T-B' + str(self.pieces[index + self.columns].id)
                edges.add(edge)
        return edges

    def confident_edges_set(self):
        edges = set()
        # For each two adjacent pieces in rows
        for i in range(self.rows):
            for j in range(self.columns - 1):
                ids = (self[i][j].id, self[i][j + 1].id)
                edge = str(self[i][j].id) + 'L-R' + str(self[i][j + 1].id)
                if edge in db_update.edges_confidence and db_update.edges_confidence[edge] >= 0.618:
                    edges.add(edge)
        # For each two adjacent pieces in columns
        for i in range(self.rows - 1):
            for j in range(self.columns):
                ids = (self[i][j].id, self[i + 1][j].id)
                edge = str(self[i][j].id) + 'T-B' + str(self[i + 1][j].id)
                if edge in db_update.edges_confidence and db_update.edges_confidence[edge] >= 0.618:
                    edges.add(edge)
        return edges

    def compute_correct_links(self):
        correct_links = 0
        for index in range(len(self.pieces)):
            if index % self.columns < self.columns - 1:
                if self.pieces[index + 1].id == self.pieces[index].id + 1:
                    correct_links += 1
            if index < (self.rows - 1) * self.columns:
                if self.pieces[index + self.columns].id == self.pieces[index].id + self.columns:
                    correct_links += 1
        return correct_links

    def compute_correct_links_percentage(self):
        correct_links = self.compute_correct_links() * 1.0
        total_links = (2 * self.rows * self.columns - self.rows - self.columns) * 1.0
        return correct_links / total_links


    def piece_size(self):
        """Returns single piece size"""
        return self.pieces[0].size

    def piece_by_id(self, identifier):
        """"Return specific piece from individual"""
        return self.pieces[self._piece_mapping[identifier]]

    def to_image(self):
        """Converts individual to showable image"""
        pieces = [piece.image for piece in self.pieces]
        return image_helpers.assemble_image(pieces, self.rows, self.columns)

    def edge(self, piece_id, orientation):
        edge_index = self._piece_mapping[piece_id]

        if (orientation == "T") and (edge_index >= self.columns):
            return self.pieces[edge_index - self.columns].id

        if (orientation == "R") and (edge_index % self.columns < self.columns - 1):
            return self.pieces[edge_index + 1].id

        if (orientation == "D") and (edge_index < (self.rows - 1) * self.columns):
            return self.pieces[edge_index + self.columns].id

        if (orientation == "L") and (edge_index % self.columns > 0):
            return self.pieces[edge_index - 1].id

        return None

    def is_solution(self):
        if self._is_solution is None:
            for i in range(len(self.pieces)-1):
                if self.pieces[i].id >= self.pieces[i+1].id:
                    self._is_solution = False
                    break
            else:
                self._is_solution = True
        
        return self._is_solution

    def get_pieces_id_list(self):
        return [piece.id for piece in self.pieces]

    def to_json_data(self, generation, start_time):
        ret = dict(
            round_id = Config.round_id,
            is_solution = self.is_solution(),
            pieces = [piece.id for piece in self.pieces],
            generation = generation,
            start_time = start_time,
            objective = self.objective,
            )
        if Config.fitness_func is not None:
            ret['fitness'] = self.fitness
        return ret
    
    def calc_rank_fitness(self, rank):
        self._fitness = 2.0 - Config.rank_based_MAX + 2.0 * (Config.rank_based_MAX - 1.0) * rank / (Config.population - 1)