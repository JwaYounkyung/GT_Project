import numpy as np
import random
import tensorflow as tf
import maddpg_o.maddpg_local.common.tf_util as U

from maddpg_o.maddpg_local.common.distributions import make_pdtype
from maddpg_o.maddpg_local import AgentTrainer
from maddpg_o.maddpg_local.micro.n_replay_buffer import ReplayBuffer
from itertools import chain
import time
import copy


def discount_with_dones(rewards, dones, gamma):
    discounted = []
    r = 0
    for reward, done in zip(rewards[::-1], dones[::-1]):
        r = reward + gamma*r
        r = r*(1.-done)
        discounted.append(r)
    return discounted[::-1]

def make_update_exp(vals, target_vals):
    polyak = 1.0 - 1e-2
    expression = []
    for var, var_target in zip(sorted(vals, key=lambda v: v.name), sorted(target_vals, key=lambda v: v.name)):
        expression.append(var_target.assign(polyak * var_target + (1.0-polyak) * var))
    expression = tf.group(*expression)
    return U.function([], [], updates=[expression])

def p_train(make_obs_ph_n, act_space_n, p_index, p_func, p_nature_func, q_func, optimizer, optimizer_nature, grad_norm_clipping=None, local_q_func=False, num_units=64, scope="trainer", reuse=None):
    with tf.variable_scope(scope, reuse=reuse):
        # print(scope, p_index)
        # create distribtuions

        # class Attention(object):
        #     def __init__(self):
        #         self.good_attn = None
        #         self.adv_attn = None
        #
        #     def handle(self, good_attn, adv_attn):
        #         self.good_attn = good_attn
        #         self.adv_attn = adv_attn


        # attn = Attention()

        act_pdtype_n = [make_pdtype(act_space) for act_space in act_space_n]

        # set up placeholders
        obs_ph_n = make_obs_ph_n
        act_ph_n = [act_pdtype_n[i].sample_placeholder([None], name="action"+str(i)) for i in range(len(act_space_n))]

        p_input = obs_ph_n[p_index]
        p_nature_input = tf.concat(obs_ph_n + act_ph_n, 1) # not sure...

        # print("p_train/p_func:", scope)
        p = p_func(p_input, int(act_pdtype_n[p_index].param_shape()[0]), scope="p_func", num_units=num_units)
        p_func_vars = U.scope_vars(U.absolute_scope_name("p_func"))
        p_nature = p_nature_func(p_nature_input, 1, scope="p_nature_func", num_units=num_units)
        p_nature_func_vars = U.scope_vars(U.absolute_scope_name("p_nature_func"))

        # wrap parameters in distribution
        act_pd = act_pdtype_n[p_index].pdfromflat(p)

        act_sample = act_pd.sample()
        p_reg = tf.reduce_mean(tf.square(act_pd.flatparam()))

        act_input_n = act_ph_n + []
        act_input_n[p_index] = act_pd.sample()
        q_input = tf.concat(obs_ph_n + act_input_n, 1)
        if local_q_func:
            q_input = tf.concat([obs_ph_n[p_index], act_input_n[p_index]], 1)
        q = q_func(q_input, 1, scope="q_func", reuse=True, num_units=num_units)[:,0]
        pg_loss = -tf.reduce_mean(q)

        loss = pg_loss + p_reg * 1e-3
        
        ### R-MADDPG loss ###
        mse_weight = 1.0
        perturbed_rewards = tf.placeholder(tf.float32, [None], name="perturbed") # placeholder for perturbed_rewards
        pred_rewards = p_nature_func(p_nature_input, 1, scope="p_nature_func", num_units=num_units)[:,0]
        loss_nature = tf.reduce_mean(pred_rewards) + mse_weight * tf.reduce_mean(tf.square(pred_rewards - perturbed_rewards))
        #####################

        optimize_expr = U.minimize_and_clip(optimizer, loss, p_func_vars, grad_norm_clipping)
        optimize_expr_nature = U.minimize_and_clip(optimizer_nature, loss_nature, p_nature_func_vars, grad_norm_clipping)

        # Create callable functions
        train = U.function(inputs=obs_ph_n + act_ph_n, outputs=loss, updates=[optimize_expr])
        train_nature = U.function(inputs=obs_ph_n + act_ph_n + [perturbed_rewards], outputs=loss_nature, updates=[optimize_expr_nature])
        act = U.function(inputs=[obs_ph_n[p_index]], outputs=act_sample)
        # attention = U.function(inputs=[obs_ph_n[p_index]], outputs=[attn.good_attn, attn.adv_attn])
        p_values = U.function([obs_ph_n[p_index]], p)
        # print([obs_ph_n[p_index]], act_sample)

        # target network
        target_p = p_func(p_input, int(act_pdtype_n[p_index].param_shape()[0]), scope="target_p_func", num_units=num_units)
        target_p_func_vars = U.scope_vars(U.absolute_scope_name("target_p_func"))
        update_target_p = make_update_exp(p_func_vars, target_p_func_vars)
        target_p_nature = p_nature_func(p_nature_input, 1, scope="target_p_nature_func", num_units=num_units)
        target_p_nature_func_vars = U.scope_vars(U.absolute_scope_name("target_p_nature_func"))
        update_target_p_nature = make_update_exp(p_nature_func_vars, target_p_nature_func_vars)

        target_act_sample = act_pdtype_n[p_index].pdfromflat(target_p).sample()
        target_act = U.function(inputs=[obs_ph_n[p_index]], outputs=target_act_sample)

        return act, train, train_nature, update_target_p, update_target_p_nature, {'p_values': p_values, 'target_act': target_act}

# def group_p_train(make_obs_ph_n, act_space_n, p_index, num_adversaries, p_func, q_func, optimizer, grad_norm_clipping=None, local_q_func=False, num_units=64, scope="trainer", reuse=None):
#     with tf.variable_scope(scope, reuse=reuse):
#         # create distribtuions
#         act_pdtype_n = [make_pdtype(act_space) for act_space in act_space_n]

#         # set up placeholders for a group
#         obs_ph_n = make_obs_ph_n
#         n_agents = len(obs_ph_n)

#         if(p_index<num_adversaries):
#             act_ph_ns = [[act_pdtype_n[i].sample_placeholder([None], name="action"+str(n)+'_'+str(i)) for i in range(len(act_space_n))] for n in range(num_adversaries)]
#         else:
#             act_ph_ns = [[act_pdtype_n[i].sample_placeholder([None], name="action"+str(n)+'_'+str(i)) for i in range(len(act_space_n))] for n in range(n_agents-num_adversaries)]
#         act_ph_ns_flatten = list(chain.from_iterable(act_ph_ns))

#         # p_input = obs_ph_n[p_index] # one obs for a certain p_index
#         # batchify obs for all agents in a group
#         if(p_index < num_adversaries): # adv
#             p_input = tf.concat(obs_ph_n[:num_adversaries], 1)
#             p_input = tf.reshape(p_input, [-1, p_input.shape[-1].value//num_adversaries])
#         else: # good agent
#             p_input = tf.concat(obs_ph_n[num_adversaries:], 1)
#             p_input = tf.reshape(p_input, [-1, p_input.shape[-1].value//(n_agents-num_adversaries)])

#         # get all actions from a group
#         p = p_func(p_input, int(act_pdtype_n[p_index].param_shape()[0]), scope="p_func", num_units=num_units)
#         p_func_vars = U.scope_vars(U.absolute_scope_name("p_func"))

#         # wrap parameters in distribution
#         # un-batchify actions from a group
#         if(p_index < num_adversaries):
#             p = tf.reshape(p, [-1, p.shape[-1]*num_adversaries])
#             ps = tf.split(p, num_or_size_splits=num_adversaries, axis=1)
#         else:
#             p = tf.reshape(p, [-1, p.shape[-1]*(n_agents-num_adversaries)])
#             ps = tf.split(p, num_or_size_splits=(n_agents-num_adversaries), axis=1)

#         # get probability distributions and action samples for a group
#         if(p_index < num_adversaries):
#             act_pds = [act_pdtype_n[i].pdfromflat(ps[i]) for i in range(num_adversaries)]
#             act_samples = [act_pds[i].sample() for i in range(num_adversaries)]
#         else:
#             act_pds = [act_pdtype_n[i].pdfromflat(ps[i-num_adversaries]) for i in range(num_adversaries, n_agents)]
#             act_samples = [act_pds[i].sample() for i in range(n_agents-num_adversaries)]
#         # act_pd = act_pdtype_n[p_index].pdfromflat(p)
#         # act_sample = act_pd.sample()

#         # p_reg = tf.reduce_mean(tf.square(act_pd.flatparam()))
#         # get average p_reg for a group
#         p_reg = tf.reduce_mean(tf.square(tf.concat([act_pd.flatparam() for act_pd in act_pds], -1)))


#         # act_input_n = act_ph_n + []
#         act_input_ns = act_ph_ns
#         # act_input_n[p_index] = act_pd.sample()
#         # q_input = tf.concat(obs_ph_n + act_input_n, 1)
#         if(p_index < num_adversaries):
#             q_inputs = []
#             for i in range(num_adversaries):
#                 act_input_ns[i][i] = act_pds[i].sample()
#                 q_inputs.append(tf.concat(obs_ph_n + act_input_ns[i], 1))
#             # batchify q_input
#             q_input = tf.concat(q_inputs, 0)
#         else:
#             q_inputs = []
#             for i in range(n_agents-num_adversaries):
#                 act_input_ns[i][i+num_adversaries] = act_pds[i].sample()
#                 q_inputs.append(tf.concat(obs_ph_n + act_input_ns[i], 1))
#             # batchify q_input
#             q_input = tf.concat(q_inputs, 0)

#         # if local_q_func:
#         #     q_input = tf.concat([obs_ph_n[p_index], act_input_n[p_index]], 1)

#         # input group of q_input into q_func
#         q = q_func(q_input, 1, scope="q_func", reuse=True, num_units=num_units)[:,0]
#         pg_loss = -tf.reduce_mean(q)

#         loss = pg_loss + p_reg * 1e-3

#         optimize_expr = U.minimize_and_clip(optimizer, loss, p_func_vars, grad_norm_clipping)

#         # Create callable functions
#         train = U.function(inputs=obs_ph_n + act_ph_ns_flatten, outputs=loss, updates=[optimize_expr])
#         if(p_index < num_adversaries):
#             # print([obs_ph_n[p_index]], act_samples[p_index])
#             act = U.function(inputs=[obs_ph_n[p_index]], outputs=act_samples[p_index])
#             p_values = U.function([obs_ph_n[p_index]], ps[p_index])
#         else:
#             # print([obs_ph_n[p_index]], act_samples[p_index-num_adversaries])
#             act = U.function(inputs=[obs_ph_n[p_index]], outputs=act_samples[p_index-num_adversaries])
#             p_values = U.function([obs_ph_n[p_index]], ps[p_index-num_adversaries])

#         # target network for a group
#         if(p_index < num_adversaries):
#             p_input = tf.reshape(p_input, [-1, p_input.shape[-1]*num_adversaries])
#             p_inputs = tf.split(p_input, num_or_size_splits=num_adversaries, axis=1)
#             target_p = p_func(p_inputs[p_index], int(act_pdtype_n[p_index].param_shape()[0]), scope="target_p_func", num_units=num_units)
#         else:
#             p_input = tf.reshape(p_input, [-1, p_input.shape[-1]*(n_agents-num_adversaries)])
#             p_inputs = tf.split(p_input, num_or_size_splits=(n_agents-num_adversaries), axis=1)
#             target_p = p_func(p_inputs[p_index-num_adversaries], int(act_pdtype_n[p_index].param_shape()[0]), scope="target_p_func", num_units=num_units)
#         # target_p = p_func(p_input, int(act_pdtype_n[p_index].param_shape()[0]), scope="target_p_func", num_units=num_units)
#         target_p_func_vars = U.scope_vars(U.absolute_scope_name("target_p_func"))
#         update_target_p = make_update_exp(p_func_vars, target_p_func_vars)

#         target_act_sample = act_pdtype_n[p_index].pdfromflat(target_p).sample()
#         target_act = U.function(inputs=[obs_ph_n[p_index]], outputs=target_act_sample)

#         return act, train, update_target_p, {'p_values': p_values, 'target_act': target_act}

def q_train(make_obs_ph_n, act_space_n, q_index, q_func, p_nature_func, optimizer, grad_norm_clipping=None, local_q_func=False, scope="trainer", reuse=None, num_units=64):
    with tf.variable_scope(scope, reuse=reuse):
        # create distribtuions
        act_pdtype_n = [make_pdtype(act_space) for act_space in act_space_n]

        # set up placeholders
        n = len(act_space_n)
        obs_ph_n = make_obs_ph_n
        act_ph_n = [act_pdtype_n[i].sample_placeholder([None], name="action"+str(i)) for i in range(n)]
        # exclude_i = list(filter(lambda i: i != q_index, range(n)))
        # mean_act = [act_ph_n[q_index], tf.reduce_mean([act_ph_n[i] for i in exclude_i])]
        target_ph = tf.placeholder(tf.float32, [None], name="target")
        #print(act_ph_n)
        q_input = tf.concat(obs_ph_n + act_ph_n, 1)
        if local_q_func:
            q_input = tf.concat([obs_ph_n[q_index], act_ph_n[q_index]], 1)
        q = q_func(q_input, 1, scope="q_func", num_units=num_units)[:,0]
        q_func_vars = U.scope_vars(U.absolute_scope_name("q_func"))

        
        # R-MADDPG
        rew = p_nature_func(q_input, 1, scope="p_nature_func", num_units=num_units)[:,0]
#         target_ph += rew
        
        q_loss = tf.reduce_mean(tf.square(q - target_ph - rew))

        # viscosity solution to Bellman differential equation in place of an initial condition
        q_reg = tf.reduce_mean(tf.square(q))
        loss = q_loss #+ 1e-3 * q_reg

        # print(reuse)
        optimize_expr = U.minimize_and_clip(optimizer, loss, q_func_vars, grad_norm_clipping)

        # Create callable functions
        train = U.function(inputs=obs_ph_n + act_ph_n + [target_ph], outputs=loss, updates=[optimize_expr])
        q_values = U.function(obs_ph_n + act_ph_n, q)

        # target network
        target_q = q_func(q_input, 1, scope="target_q_func", num_units=num_units)[:,0]
        target_q_func_vars = U.scope_vars(U.absolute_scope_name("target_q_func"))
        update_target_q = make_update_exp(q_func_vars, target_q_func_vars)

        target_q_values = U.function(obs_ph_n + act_ph_n, target_q)

        return train, update_target_q, {'q_values': q_values, 'target_q_values': target_q_values}

class RMADDPGAgentTrainer(AgentTrainer):
    def __init__(self, name, model, obs_shape_n, act_space_n, agent_index, args, local_q_func=False):
        self.name = name
        self.n = len(obs_shape_n)
        self.agent_index = agent_index
        self.args = args
        obs_ph_n = []
        for i in range(self.n):
            obs_ph_n.append(U.BatchInput(obs_shape_n[i], name="observation"+str(i)).get())

        # Create all the functions necessary to train the model
        self.q_train, self.q_update, self.q_debug = q_train(
            scope=self.name,
            make_obs_ph_n=obs_ph_n,
            act_space_n=act_space_n,
            q_index=agent_index,
            q_func=model,
            optimizer=tf.train.AdamOptimizer(learning_rate=args.lr),
            grad_norm_clipping=0.5,
            local_q_func=local_q_func,
            num_units=args.num_units
        )
        self.act, self.p_train, self.p_update, self.p_debug = p_train(
            scope=self.name,
            make_obs_ph_n=obs_ph_n,
            act_space_n=act_space_n,
            p_index=agent_index,
            p_func=model,
            q_func=model,
            optimizer=tf.train.AdamOptimizer(learning_rate=args.lr),
            grad_norm_clipping=0.5,
            local_q_func=local_q_func,
            num_units=args.num_units
        )
        # Create experience buffer
        self.replay_buffer = ReplayBuffer(1e6)
        self.max_replay_buffer_len = args.batch_size * args.max_episode_len
        self.replay_sample_index = None

    def action(self, obs):
        # print(obs[None].shape)
        return self.act(obs[None])[0]

    def experience(self, obs, act, rew, new_obs, done, terminal):
        # Store transition in the replay buffer.
        self.replay_buffer.add(obs, act, rew, new_obs, float(done))

    def preupdate(self):
        self.replay_sample_index = None

    def update(self, agents, t, group_train=False):
        if len(self.replay_buffer) < self.max_replay_buffer_len: # replay buffer is not large enough
            return
        if not t % 100 == 0:  # only update every 100 steps
            return

        self.replay_sample_index = self.replay_buffer.make_index(self.args.batch_size)
        # collect replay sample from all agents
        obs_n = []
        obs_next_n = []
        act_n = []
        index = self.replay_sample_index
        for i in range(self.n):
            obs, act, rew, obs_next, done = agents[i].replay_buffer.sample_index(index)
            obs_n.append(obs)
            obs_next_n.append(obs_next)
            act_n.append(act)
        obs, act, rew, obs_next, done = self.replay_buffer.sample_index(index)

        # train q network
        num_sample = 1
        target_q = 0.0
        for i in range(num_sample):
            target_act_next_n = [agents[i].p_debug['target_act'](obs_next_n[i]) for i in range(self.n)]
            target_q_next = self.q_debug['target_q_values'](*(obs_next_n + target_act_next_n))
            target_q += rew + self.args.gamma * (1.0 - done) * target_q_next
        target_q /= num_sample
        q_loss = self.q_train(*(obs_n + act_n + [target_q]))

        # train p network
        p_loss = self.p_train(*(obs_n + act_n))

        self.p_update()
        self.q_update()

        return [q_loss, p_loss, np.mean(target_q), np.mean(rew), np.mean(target_q_next), np.std(target_q)]

class RMADDPGAgentSharedTrainer(AgentTrainer):
    def __init__(self, name, model, obs_shape_n, act_space_n, agent_index, num_adversaries, args, local_q_func=False):
        self.name = name
        self.n = len(obs_shape_n)
        self.agent_index = agent_index
        self.num_adversaries = num_adversaries
        self.args = args
        obs_ph_n = []
        for i in range(self.n):
            obs_ph_n.append(U.BatchInput(obs_shape_n[i], name="observation"+str(i)).get())


        # Create all the functions necessary to train the model
        # print("!#12")
        self.q_train, self.q_update, self.q_debug = q_train(
            scope=self.name,
            make_obs_ph_n=obs_ph_n,
            act_space_n=act_space_n,
            q_index=agent_index,
            q_func=model,
            optimizer=tf.train.AdamOptimizer(learning_rate=args.lr),
            grad_norm_clipping=0.5,
            local_q_func=local_q_func,
            num_units=args.num_units,
            reuse=tf.AUTO_REUSE
        )
        self.act, self.p_train, self.p_update, self.p_debug = group_p_train(
            scope=self.name,
            make_obs_ph_n=obs_ph_n,
            act_space_n=act_space_n,
            p_index=agent_index,
            num_adversaries=num_adversaries,
            p_func=model,
            q_func=model,
            optimizer=tf.train.AdamOptimizer(learning_rate=args.lr),
            grad_norm_clipping=0.5,
            local_q_func=local_q_func,
            num_units=args.num_units,
            reuse=tf.AUTO_REUSE
        )

        # Create experience buffer
        self.replay_buffer = ReplayBuffer(1e6)
        self.max_replay_buffer_len = args.batch_size * args.max_episode_len
        self.replay_sample_index = None

    def action(self, obs):

        return self.act(obs[None])[0]

    def experience(self, obs, act, rew, new_obs, done, terminal):
        # Store transition in the replay buffer.
        self.replay_buffer.add(obs, act, rew, new_obs, float(done))

    def preupdate(self):
        self.replay_sample_index = None

    def update(self, agents, t, group_train=False):
        if len(self.replay_buffer) < self.max_replay_buffer_len: # replay buffer is not large enough
            return
        if not t % 100 == 0:  # only update every 100 steps
            return

        self.replay_sample_index = self.replay_buffer.make_index(self.args.batch_size)
        # collect replay sample from all agents
        obs_n = []
        obs_next_n = []
        act_n = []
        index = self.replay_sample_index
        for i in range(self.n):
            obs, act, rew, obs_next, done = agents[i].replay_buffer.sample_index(index)
            obs_n.append(obs)
            obs_next_n.append(obs_next)
            act_n.append(act)
        obs, act, rew, obs_next, done = self.replay_buffer.sample_index(index)

        # train q network
        num_sample = 1
        target_q = 0.0
        for i in range(num_sample):
            target_act_next_n = [agents[i].p_debug['target_act'](obs_next_n[i]) for i in range(self.n)]
            target_q_next = self.q_debug['target_q_values'](*(obs_next_n + target_act_next_n))
            target_q += rew + self.args.gamma * (1.0 - done) * target_q_next
        target_q /= num_sample
        q_loss = self.q_train(*(obs_n + act_n + [target_q]))

        # train p network
        if(group_train):
            p_loss = self.group_p_train(*(obs_n + act_n))
        else:
            p_loss = self.p_train(*(obs_n + act_n))

        self.p_update()
        self.q_update()

        return [q_loss, p_loss, np.mean(target_q), np.mean(rew), np.mean(target_q_next), np.std(target_q)]

class RMADDPGAgentMicroSharedTrainer(AgentTrainer):
    def __init__(self, name, model_nature, model_p, model_q, obs_shape_n, act_space_n, agent_index, args, num_units, local_q_func=False):
        self.name = name
        self.n = len(obs_shape_n)
        self.agent_index = agent_index
        # self.args = args
        obs_ph_n = []
        for i in range(self.n):
            obs_ph_n.append(U.BatchInput(obs_shape_n[i], name="observation"+str(i)).get())
        # import dill
        # dill.dump(obs_ph_n, open("tmp", "wb"))

        # Create all the functions necessary to train the model
        # print(num_units)
        self.q_train, self.q_update, self.q_debug = q_train(
            scope=self.name,
            make_obs_ph_n=obs_ph_n,
            act_space_n=act_space_n,
            q_index=agent_index,
            q_func=model_q,
            p_nature_func=model_nature,
            optimizer=tf.train.AdamOptimizer(learning_rate=args.lr),
            grad_norm_clipping=0.5,
            local_q_func=local_q_func,
            num_units=num_units,
            reuse=tf.AUTO_REUSE
        )
        self.act, self.p_train, self.p_nature_train, self.p_update, self.p_nature_update, self.p_debug = p_train(
            scope=self.name,
            make_obs_ph_n=obs_ph_n,
            act_space_n=act_space_n,
            p_index=agent_index,
            p_func=model_p,
            p_nature_func=model_nature,
            q_func=model_q,
            optimizer=tf.train.AdamOptimizer(learning_rate=args.lr),
            optimizer_nature=tf.train.AdamOptimizer(learning_rate=args.lr_nature),
            grad_norm_clipping=0.5,
            local_q_func=local_q_func,
            num_units=num_units,
            reuse=tf.AUTO_REUSE
        )
        # Create experience buffer
        self.replay_buffer = ReplayBuffer(1e6)
        self.max_replay_buffer_len = args.batch_size * args.max_episode_len
        self.replay_sample_index = None
        self.gamma = args.gamma

    def get_attn(self, obs):
        attn = self.attention(obs[None])
        return attn[0][0], attn[1][0]

    def batch_attn(self, obs):
        return self.attention(obs)

    def action(self, obs):
        #print(obs[None].shape)
        return self.act(obs[None])[0]

    def batch_action(self, obs):
        return self.act(obs)

    # def target_action(self, obs):
    #     print (self.p_debug['target_act'](obs[None]))
    #     return self.p_debug['target_act'](obs[None])

    def experience(self, obs, act, rew, new_obs, done, terminal):
        # Store transition in the replay buffer.
        self.replay_buffer.add(obs, act, rew, new_obs, float(done))

    def preupdate(self):
        self.replay_sample_index = None

    def target_action(self, batch_obs):
        return self.p_debug['target_act'](batch_obs)

    def update(self, data, target_act_next_n, group_train=False):
        # times = []
        # if len(self.replay_buffer) < self.max_replay_buffer_len: # replay buffer is not large enough
        #     return
        # if not t % 100 == 0:  # only update every 100 steps
        #     return

        # self.replay_sample_index = self.replay_buffer.make_index(self.args.batch_size)
        # collect replay sample from all agents
        # obs_n = []
        # obs_next_n = []
        # act_n = []
        # index = self.replay_sample_index
        # for i in range(self.n):
        #     obs, act, rew, obs_next, done = agents[i].replay_buffer.sample_index(index)
        #     obs_n.append(obs)
        #     obs_next_n.append(obs_next)
        #     act_n.append(act)
        # n * batch * shape
        obs_n, act_n, rew, obs_next_n, done = data
        obs_n = list(obs_n)
        act_n = list(act_n)
        obs_next_n = list(obs_next_n)
        # done = np.array(done)
        # target_act_next_n = copy.deepcopy(target_act_next_n)

        # train q network
        num_sample = 1
        target_q = 0.0
        for i in range(num_sample):
            # target_act_next_n = [agents[i].p_debug['target_act'](obs_next_n[i]) for i in range(self.n)]
            # tmp0 = time.time()
            # print(type(obs_next_n))
            # print(type(target_act_next_n))
            target_q_next = self.q_debug['target_q_values'](*(obs_next_n + target_act_next_n))
            # times.append(time.time() - tmp0)  # 62s
            target_q += self.gamma * (1.0 - done) * target_q_next
        target_q /= num_sample

        
        # tmp0 = time.time()
        q_loss = self.q_train(*(obs_n + act_n + [target_q]))
        # times.append(time.time() - tmp0)  # 161s

        # train p network
        # tmp0 = time.time()
        p_loss = self.p_train(*(obs_n + act_n))
        p_loss_nature = self.p_nature_train(*(obs_n + act_n + [rew])) # rew or [rew]?
        # times.append(time.time() - tmp0)  # 166s

        # tmp0 = time.time()
        self.p_update()
        # times.append(time.time() - tmp0)  # 4s

        # tmp0 = time.time()
        self.q_update()
        # times.append(time.time() - tmp0)  # 10s
        self.p_nature_update()

        # return [q_loss, p_loss, np.mean(target_q), np.mean(rew), np.mean(target_q_next), np.std(target_q), times]
        return []
