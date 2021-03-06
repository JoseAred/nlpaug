try:
    import torch
    import torch.nn.functional as F
except ImportError:
    # No installation required if not using this function
    pass
import numpy as np

import nlpaug.util.selection.filtering as filtering


class LanguageModels:
    OPTIMIZE_ATTRIBUTES = ['external_memory', 'return_proba']

    def __init__(self, device=None, temperature=1.0, top_k=100, top_p=0.01, optimize=None):
        try:
            self.device = 'cuda' if device is None and torch.cuda.is_available() else device
        except NameError:
            raise ImportError('Missed torch, transformers libraries. Install torch by following https://pytorch.org/get-started/locally/ and transfomers by '
                              '`pip install transformers`')
        self.temperature = temperature
        self.top_k = top_k
        self.top_p = top_p
        self.optimize = self.init_optimize(optimize)

    @classmethod
    def get_default_optimize_config(cls):
        return {
            'external_memory': 1024,  # GPT2 needs either zero or non-zero. XLNet needs number of extra memory tokens.
            'return_proba': False
        }

    def init_optimize(self, optimize):
        _optimize = self.get_default_optimize_config()
        if optimize is None:
            return _optimize

        for attr in self.OPTIMIZE_ATTRIBUTES:
            if attr in optimize:
                _optimize[attr] = optimize[attr]

        return _optimize

    def clean(self, text):
        return text.strip()

    def predict(self, text, target_word=None, n=1):
        raise NotImplementedError

    @classmethod
    def control_randomness(cls, logits, seed):
        temperature = seed['temperature']
        if temperature is not None:
            return logits / temperature
        return logits

    def filtering(self, logits, seed):
        top_k = seed['top_k']
        top_p = seed['top_p']

        check_top_k = False
        check_top_p = False

        if top_k is not None and 0 < top_k < len(logits):
            logits, idxes = filtering.filter_top_k(logits, top_k, replace=-float('Inf'))
            check_top_k = True
        if top_p is not None and 0 < top_p < 1:
            logits, idxes = filtering.nucleus_sampling(logits, top_p)
            check_top_p = True

        # If top_p is not None, value will be sorted, so no need to select it again
        if not check_top_p:
            if check_top_k:
                logits = logits.index_select(0, idxes)
                if self.device == 'cuda':
                    idxes = idxes.cpu()
                idxes = idxes.detach().numpy().tolist()
            else:
                idxes = np.arange(len(logits)).tolist()
        else:
            logits = logits[:len(idxes)]
            if self.device == 'cuda':
                idxes = idxes.cpu()
            idxes = idxes.detach().numpy().tolist()

        return logits, idxes

    def pick(self, logits, idxes, target_word, n=1):
        candidate_ids, candidate_probas = self.prob_multinomial(logits, n=n*10)
        candidate_ids = [idxes[candidate_id] for candidate_id in candidate_ids]
        results = self.get_candidiates(candidate_ids, candidate_probas, target_word, n)

        return results

    def id2token(self, _id):
        raise NotImplementedError()

    def prob_multinomial(self, logits, n):
        # Convert to probability
        probas = F.softmax(logits, dim=-1)

        # Draw candidates
        num_sample = min(n, torch.nonzero(probas).size(0), as_tuple=False)  # Number of potential candidate is small when top_k/ top_p are used.
        filtered_top_n_ids = torch.multinomial(probas, num_samples=num_sample, replacement=False).tolist()
        # filtered_top_n_ids = np.random.choice(probas.size(0), num_sample, False, probas.cpu().numpy()).tolist()

        if self.optimize['return_proba']:
            top_n_probas = [probas[_id] for _id in filtered_top_n_ids]
            return filtered_top_n_ids, top_n_probas

        return filtered_top_n_ids, None

    def is_skip_candidate(self, candidate):
        return False

    def get_candidiates(self, candidate_ids, candidate_probas, target_word=None, n=1):
        # To have random behavior, NO sorting for candidate_probas.
        results = []
        if candidate_probas is None:
            candidate_probas = [0] * len(candidate_ids)

        for candidate_id, candidate_proba in zip(candidate_ids, candidate_probas):
            candidate_word = self.id2token(candidate_id)

            if candidate_word == '':
                continue

            if target_word is not None and candidate_word.lower() == target_word.lower():
                continue

            if self.is_skip_candidate(candidate_word):
                continue

            results.append((candidate_word, candidate_proba))

            if len(results) >= n:
                break

        return results
