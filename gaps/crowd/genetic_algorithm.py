from __future__ import print_function
import time
import random
from operator import attrgetter
from gaps import image_helpers
from gaps.selection import roulette_selection
# from gaps.plot import Plot
from gaps.progress_bar import print_progress
from gaps.crowd.crossover import Crossover
from gaps.crowd.individual import Individual
from gaps.crowd.nodes import NodesAndHints
from gaps.crowd.crowd_individual import CrowdIndividual
from gaps.crowd.image_analysis import ImageAnalysis
from gaps.crowd.fitness import db_update
from gaps.crowd.dbaccess import JsonDB, mongo_wrapper
from gaps.config import Config
from multiprocessing import Process, Queue
import redis
import json

redis_cli = redis.Redis(connection_pool=Config.pool)

def worker(data_q, res_q):
    while True:
        best_match_table, parents = data_q.get(block=True)
        ImageAnalysis.best_match_table = best_match_table
        children = []
        for first_parent, second_parent in parents:
            crossover = Crossover(first_parent, second_parent)
            crossover.run()
            child = crossover.child()
            child.is_solution() # is_solution is a decorated function. calculate here.
            children.append(child)
        res_q.put(children, block=True)
    # return children, has_solution, solution

def refreshTimeStamp(start_time):
    Config.timestamp = (time.time() - start_time) * 1000
    if not Config.cli_args.online:
        Config.timestamp += mongo_wrapper.get_round_winner_time_milisecs() * Config.offline_start_percent * 1.0

def compute_edges_match(individual, columns, edges):
    edges_match = 0.0
    confidence_edges_match = 0.0
    unconfidence_edges_match = 0.0
    correct_edges_match = 0.0
    confidence_edges = 0.0
    unconfidence_edges = 0.0
    correct_edges = 0.0
    for e in edges:
        edge = edges[e]
        first_piece_id, second_piece_id = int(e.split('-')[0][:-1]), int(e.split('-')[1][1:])
        edges_matched = False
        correct_edge = False
        if e.split('-')[0][-1] == 'L':
            if second_piece_id == first_piece_id + 1:
                correct_edge = True
                correct_edges += 1
            if individual.edge(first_piece_id, 'R') == second_piece_id:
                edges_matched = True
                edges_match += 1
                if correct_edge:
                    correct_edges_match += 1
        else:
            if second_piece_id == first_piece_id + columns:
                correct_edge = True
                correct_edges += 1
            if individual.edge(first_piece_id, 'D') == second_piece_id:
                edges_matched = True
                edges_match += 1
                if correct_edge:
                    correct_edges_match += 1
        
        wp = float(edge['wp'])
        wn = float(edge['wn'])
        confidence = wp * 1.0 / (wn + wp)
        if confidence >= 0.618:
            confidence_edges += 1
            if edges_matched:
                confidence_edges_match += 1
        else:
            unconfidence_edges += 1
            if edges_matched:
                unconfidence_edges_match += 1

    len_edges = len(edges)
    correct_edges = 1.0 if correct_edges == 0 else correct_edges
    unconfidence_edges = 1.0 if unconfidence_edges == 0 else unconfidence_edges
    confidence_edges = 1.0 if confidence_edges == 0 else confidence_edges
    len_edges = 1.0 if len_edges == 0 else len_edges

    return correct_edges_match / correct_edges, unconfidence_edges_match / unconfidence_edges, \
        confidence_edges_match / confidence_edges, edges_match / len_edges


# Don't create two instantces for this class
class GeneticAlgorithm(object):

    TERMINATION_THRESHOLD = 10

    def __init__(self, image, piece_size, population_size, generations, r, c):
        self._image = image
        self._piece_size = piece_size
        self._generations = generations
        self._elite_size = Config.elite_size
        pieces, rows, columns = image_helpers.flatten_image(image, piece_size, indexed=True, r=r, c=c)
        self.rows = rows
        self.columns = columns
        self._population = [Individual(pieces, rows, columns) for _ in range(population_size)]
        self._pieces = pieces
        self.common_edges = dict()

    def start_evolution(self, verbose):
        with open('result_file_%d.csv' % Config.round_id , 'w') as f:
            line = "%s,%s,%s,%s,%s,%s,%s\n" % ('time', 'cog_index', 'correct_in_db',
                'total_in_db', 'correct_in_GA', 'total_in_GA', 'precision')
            f.write(line)
        '''
        print("=== Pieces:      {}\n".format(len(self._pieces)))
        '''
        
        if verbose:
            from gaps.plot import Plot
            plot = Plot(self._image)

        #ImageAnalysis.analyze_image(self._pieces)

        start_time = time.time()
        old_correct_links_percentage = 0.0
        correct_links_percentage = 0.0

        fittest = None
        best_fitness_score = float("-inf")
        '''
        termination_counter = 0
        '''

        # save elites of each generation.
        if Config.cli_args.online:
            # online
            elites_db = JsonDB(collection_name='elites', doc_name='round'+str(Config.round_id))
        else:
            # offline
            collection_name = 'elites_offline_mp' if Config.multiprocess else \
                'elites_offline_pixel' if Config.use_pixel else'elites_offline'
            elites_db = JsonDB(collection_name=collection_name, doc_name='round'+str(Config.round_id)\
                +'_'+Config.fitness_func_name+'_paper_'+str(Config.rank_based_MAX)+'_skiprecom_'\
                +str(Config.population)+'_'+str(Config.elite_percentage)\
                +('_SUS' if Config.roulette_alt == True else '')+('_{}'.format(Config.use_pixel_shred)\
                if Config.use_pixel else '')+'_'+str(Config.erase_edge)+'_debug')
       
        solution_found = False

        if Config.multiprocess:
            data_q = Queue()
            res_q = Queue()
            processes = []
            for i in range(Config.process_num):
                p = Process(target=worker, args=(data_q, res_q))
                p.start()
                processes.append(p)

        old_crowd_edge_count = 1
        for generation in range(self._generations):
            if not Config.cli_args.online and not Config.cli_args.hide_detail:
                print_progress(generation, self._generations - 1, prefix="=== Solving puzzle offline: ", start_time=start_time)
            
            refreshTimeStamp(start_time)

            ## In crowd-based algorithm, we need to access database to updata fintess measure
            ## at the beginning of each generation.
            # update fitness from database.
            
            db_update()
            #print(ImageAnalysis.dissimilarity_measures)
            if not Config.cli_args.hide_detail:
                print("edge_count:{}/edge_prop:{}".format(db_update.crowd_edge_count, db_update.crowd_edge_count/Config.total_edges))
            '''
            if db_update.crowd_edge_count * 1.0 / old_crowd_edge_count > 10:
                crowdIndividual = CrowdIndividual(self._pieces, self.rows, self.columns)
                crowd_population = crowdIndividual.getIndividuals()
                self._population.extend(crowd_population)
                old_crowd_edge_count = db_update.crowd_edge_count
                print("generate individuals according to edges")
                aver_edges_match = [0.0, 0.0, 0.0, 0.0]
                for e in crowd_population:
                    cm, ucm, cem, em = compute_edges_match(e, self.columns, mongo_wrapper.cog_edges_documents(Config.timestamp))
                    aver_edges_match[0] += cm
                    aver_edges_match[1] += ucm
                    aver_edges_match[2] += cem
                    aver_edges_match[3] += em
                print('edges_match in crowd first generation', [m / len (crowd_population) for m in aver_edges_match])
            '''
            # calculate dissimilarity and best_match_table.
            ImageAnalysis.analyze_image(self._pieces)
            # fitness of all individuals need to be re-calculated.
            for _individual in self._population:
                _individual._objective = None
                _individual._fitness = None

            new_population = []

            # random.shuffle(self._population)
            self._population.sort(key=attrgetter("objective"))
            # Elitism
            # elite = self._get_elite_individuals(elites=self._elite_size)
            elite = self._population[-self._elite_size:]
            '''
            aver_edges_match = [0.0, 0.0, 0.0, 0.0]
            for e in elite:
                cm, ucm, cem, em = compute_edges_match(e, self.columns, mongo_wrapper.cog_edges_documents(Config.timestamp))
                aver_edges_match[0] += cm
                aver_edges_match[1] += ucm
                aver_edges_match[2] += cem
                aver_edges_match[3] += em
            print('edges_match in elite', [m / len (elite) for m in aver_edges_match])
            '''
            new_population.extend(elite)
            
            if Config.fitness_func_name == 'rank-based':
                #!!! self._population needs to be sorted first
                # for rank, indiv in enumerate(self._population):
                #     indiv.calc_rank_fitness(rank)
                self.calc_rank_fitness()
            
            # write elites to Json
            for e in elite:
                elites_db.add(e.to_json_data(generation, start_time))

            if solution_found:
                print("GA found a solution for round {}!".format(Config.round_id))
                if Config.cli_args.online:
                    GA_time = time.time() - (mongo_wrapper.get_round_start_milisecs() / 1000.0)
                    print("GA time: %.3f" % GA_time)
                else:
                    winner_time = mongo_wrapper.get_round_winner_time_milisecs() / 1000.0
                    GA_time = time.time() - start_time + \
                        mongo_wrapper.get_round_winner_time_milisecs() * Config.offline_start_percent / 1000.0
                    print("solved, winner time: %.3f, GA time: %.3f" % (winner_time, GA_time))
                if Config.multiprocess:
                    for p in processes:
                        p.terminate()
                exit(0)
            self._get_common_edges(elite)

            selected_parents = roulette_selection(self._population, elites=self._elite_size)

            if Config.multiprocess:
                # multiprocessing
                worker_args = []
                # assign equal amount of work to process_num-1 processes
                for i in range(Config.process_num-1):
                    worker_args.append(selected_parents[(len(selected_parents)//Config.process_num)*i \
                        : (len(selected_parents)//Config.process_num)*(i+1)])
                # assign the rest to the last process
                worker_args.append(selected_parents[(len(selected_parents)//Config.process_num)*(Config.process_num-1):len(selected_parents)])
                # t1 = time.time()
                # with Pool(processes=Config.process_num) as pool:
                #     t1 = time.time()
                #     results = pool.map(worker, worker_args)
                #     print('from mp:{}'.format(time.time()-t1))
                for i in range(Config.process_num):
                    data_q.put((ImageAnalysis.best_match_table, worker_args[i]),\
                        block=True)
                results = []
                for i in range(Config.process_num):
                    results.append(res_q.get(block=True))

            else:
                # non multiprocessing
                result = []
                for first_parent, second_parent in selected_parents:
                    crossover = Crossover(first_parent, second_parent)
                    crossover.run()
                    child = crossover.child()
                    result.append(child)
                    # if child.is_solution():
                    #     elites_db.add(child.to_json_data(generation+1, start_time))
                    #     elites_db.save()
                    #     solution_found = True
                    # new_population.append(child)
                results = [result]

            aver_edges_match = [0.0, 0.0, 0.0, 0.0]
            for result in results:
                new_population.extend(result)
                for child in result:
                    '''
                    cm, ucm, cem, em = compute_edges_match(e, self.columns, mongo_wrapper.cog_edges_documents(Config.timestamp))
                    aver_edges_match[0] += cm
                    aver_edges_match[1] += ucm
                    aver_edges_match[2] += cem
                    aver_edges_match[3] += em
                    '''

                    correct_links_percentage = child.compute_correct_links_percentage()
                    if correct_links_percentage > old_correct_links_percentage:
                        #print("time: %.6fs, correct_links_percentage: %.6f" % (time.time() - start_time, 100 * correct_links_percentage))
                        old_correct_links_percentage = correct_links_percentage
                    
                    if child.is_solution():
                        fittest = child
                        redis_key = 'round:' + str(Config.round_id) + ':GA_edges'
                        res = redis_cli.set(redis_key, json.dumps(list(child.edges_set())))
                        #print(res, list(child.edges_set()))
                        #print(compute_edges_match(child, self.columns, mongo_wrapper.cog_edges_documents(Config.timestamp)))
                        solution_found = True
                        elites_db.add(child.to_json_data(generation+1, start_time))
                        elites_db.save()
                        break
                # time_count += result[3]
                # if result[1] and not solution_found: # has solution
                #     solution_found = True
                #     elites_db.add(result[2].to_json_data(generation+1, start_time))
                #     elites_db.save()
            #print('edges_match in children', [m / sum([len(r) for r in results]) for m in aver_edges_match])

            if not solution_found:
                fittest = self._best_individual()
                if fittest.fitness > best_fitness_score:
                    best_fitness_score = fittest.fitness

            '''
            if fittest.fitness <= best_fitness_score:
                termination_counter += 1
            else:
                best_fitness_score = fittest.fitness

            if termination_counter == self.TERMINATION_THRESHOLD:
                print("\n\n=== GA terminated")
                print("=== There was no improvement for {} generations".format(self.TERMINATION_THRESHOLD))
                return fittest
            '''

            self._population = new_population
        
            if verbose:
                from gaps.plot import Plot
                plot.show_fittest(fittest.to_image(), "Generation: {} / {}".format(generation + 1, self._generations))

        elites_db.save()    
        return fittest

    def _remove_unconfident_edges(self, edges_set):
        old_size = len(edges_set)
        for e in list(edges_set):
            if e in db_update.edges_confidence and db_update.edges_confidence[e] < 0.618:
                edges_set.remove(e)
        new_size = len(edges_set)
        if old_size != new_size:
            print('remove %d edges' % (old_size - new_size))

    def _merge_common_edges(self, old_edges_set, new_edges_set):
        links = {
            'L-R': {},
            'T-B': {}
        }
        for edges_set in [old_edges_set, new_edges_set]:
            for e in edges_set:
                left, right = e.split('-')
                x, tag, y = left[:-1], 'L-R' if left[-1] == 'L' else 'T-B', right[1:]
                links[tag][x] = y
        merged_set = set()
        for orient in links:
            for x, y in links[orient].items():
                merged_set.add(x + orient + y)
        return merged_set

    def _get_common_edges(self, individuals):
        confident_edges_sets, edges_sets = [], []
        for individual in individuals:
            edges_set = individual.edges_set()
            confident_edges_set = individual.confident_edges_set()
            edges_sets.append(edges_set)
            confident_edges_sets.append(confident_edges_set)

        confident_edges_set = confident_edges_sets[0]
        for i in range(1, len(confident_edges_sets)):
            confident_edges_set = confident_edges_set | confident_edges_sets[i]

        edges_set = edges_sets[0]
        for i in range(1, len(edges_sets)):
            edges_set = edges_set & edges_sets[i]

        correct_links = 0
        #self._remove_unconfident_edges(self.common_edges)
        old_common_edges = list(self.common_edges.items())
        for k, v in old_common_edges:
            if v < 1:
                del self.common_edges[k]
            else:
                self.common_edges[k] = v / 2

        new_common_edges = self._merge_common_edges(edges_set, confident_edges_set)

        for e in new_common_edges:
            self.common_edges[e] = 32
        for e in self.common_edges.keys():
            left, right = e.split('-')
            x = int(left[:-1])
            y = int(right[1:])
            if left[-1] == 'L':
                if x + 1 == y and y % Config.cli_args.rows != 0:
                    correct_links += 1
            else:
                if x + Config.cli_args.rows == y:
                    correct_links += 1
        
        with open('result_file_%d.csv' % Config.round_id , 'a') as f:
            line = "%d,%d,%d,%d,%d,%d,%.4f\n" % (Config.timestamp, db_update.cog_index, db_update.crowd_correct_edge,
                db_update.crowd_edge_count, correct_links, len(self.common_edges), 
                0 if len(self.common_edges) == 0 else float(correct_links) / float(len(self.common_edges)))
            f.write(line)
        
        redis_key = 'round:' + str(Config.round_id) + ':GA_edges'
        redis_cli.set(redis_key, json.dumps(list(self.common_edges.keys())))
        
        print('\ntimestamp:', Config.timestamp, 'cog index:', db_update.cog_index, 
            '\ncorrect edges in db:', db_update.crowd_correct_edge, 'total edges in db:', db_update.crowd_edge_count, 
            '\ncorrect edges in GA:', correct_links, 'total edges in GA:', len(self.common_edges))
        
        return edges_set


    '''
    def _get_elite_individuals(self, elites):
        """Returns first 'elite_count' fittest individuals from population"""
        return sorted(self._population, key=attrgetter("fitness"))[-elites:]
    '''

    def _best_individual(self):
        """Returns the fittest individual from population"""
        return max(self._population, key=attrgetter("fitness"))

    def calc_rank_fitness(self):
        rank1 = 0
        while rank1 < len(self._population):
            fitness1 = Config.get_rank_fitness(rank1, len(self._population))
            indiv1 = self._population[rank1]
            rank2 = rank1 + 1
            for rank2 in range(rank1+1, len(self._population)):
                indiv2 = self._population[rank2]
                if abs(indiv1.objective-indiv2.objective) > 1e-6:
                    break
            fitness2 = Config.get_rank_fitness(rank2 - 1, len(self._population))
            for indiv in self._population[rank1: rank2]:
                indiv._fitness = (fitness1 + fitness2) / 2.0
            rank1 = rank2
